"""Fail-closed analysis of normalized, hash-bound actual soak evidence."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re

REQUIRED_SECONDS = 8 * 3600
STALE_MS = 250
FAULT_TYPES = {"isaac_crash", "wifi_drop", "uplink_disconnect"}
SHA = re.compile(r"[0-9a-f]{64}\Z")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value, minimum=0):
    return type(value) in (int, float) and math.isfinite(value) and value >= minimum


def load_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


def bound_file(root, reference):
    require(isinstance(reference, dict) and set(reference) == {"path", "sha256"}, "File reference fields differ")
    require(type(reference["path"]) is str and SHA.fullmatch(reference["sha256"]), "Invalid file reference")
    root = root.resolve()
    path = (root / reference["path"]).resolve()
    require(path.is_relative_to(root) and path.is_file(), "Evidence file outside manifest directory or absent")
    content = path.read_bytes()
    require(bool(content) and hashlib.sha256(content).hexdigest() == reference["sha256"], "Missing or changed evidence bytes")
    return path, content


def analyze_events(events):
    require(len(events) >= 2, "No receiver coverage")
    require(events[0]["kind"] == "session_start" and events[-1]["kind"] == "session_end", "Missing session boundaries")
    require(events[-1].get("completed") is True, "Session incomplete")
    start, end = events[0]["t_s"], events[-1]["t_s"]
    require(finite(start) and finite(end) and end-start >= REQUIRED_SECONDS, "Less than eight continuous hours")
    require(events[0].get("monitor") == "continuous_receiver_stale_and_freeze_detector", "Receiver detector provenance missing")
    failures, heartbeats, protected_blocks, probed_blocks = [], [], set(), set()
    resets, faults, audible, used_resets = {}, {}, set(), set()
    stale, freezes, trials, lock_count = 0, 0, 0, 0
    committed = None
    previous = last_trial = start
    for index, event in enumerate(events):
        require(isinstance(event, dict) and type(event.get("seq")) is int and event["seq"] == index, "Event sequence gap or duplicate")
        require(event.get("source_kind") == "live" and event.get("clock_domain") == "unity_monotonic", "Synthetic source or mixed event clocks")
        t = event.get("t_s")
        require(finite(t) and previous <= t <= end, "Nonmonotonic or out-of-range event timestamp")
        previous = t
        kind = event["kind"]
        if kind == "heartbeat":
            require(event.get("block") in {"teaching", "selection", "protected", "paused"}, "Unknown block")
            require(type(event.get("block_id")) is str and event["block_id"], "Block identity missing")
            require(finite(event.get("state_age_ms")) and finite(event.get("frame_age_ms")), "Missing actual receiver ages")
            require(type(event.get("mirrored_frames")) is int and event["mirrored_frames"] >= 0, "Missing mirror counter")
            if heartbeats:
                require(event["mirrored_frames"] >= heartbeats[-1]["mirrored_frames"], "Mirror counter regressed")
                if (event['block'] != 'paused' and heartbeats[-1]['block'] != 'paused'
                        and t-heartbeats[-1]['t_s'] >= .25
                        and event['mirrored_frames'] == heartbeats[-1]['mirrored_frames']):
                    failures.append('no_mirror_progress_while_active')
            heartbeats.append(event)
            if event["block"] == "protected":
                protected_blocks.add(event["block_id"])
                if event["state_age_ms"] > STALE_MS: failures.append("protected_stale_sample")
                if event["frame_age_ms"] > STALE_MS: failures.append("protected_freeze_sample")
        elif kind in {"stale_gap", "frame_freeze"}:
            require(event.get("block") in {"teaching", "selection", "protected", "paused"} and finite(event.get("duration_ms")), "Invalid gap event")
            if event["duration_ms"] > STALE_MS:
                stale += kind == "stale_gap"
                freezes += kind == "frame_freeze"
                if event["block"] == "protected": failures.append("protected_"+kind)
        elif kind == "record_commit":
            require(SHA.fullmatch(event.get("sha256", "")), "Invalid committed-record hash")
            committed = event["sha256"]
        elif kind == "fault":
            identifier = event.get("fault_id")
            require(type(identifier) is str and identifier and identifier not in faults, "Duplicate/missing fault ID")
            require(event.get("fault_type") in FAULT_TYPES and committed is not None, "Unknown fault or no committed record")
            require(event.get("last_committed_sha256") == committed, "Fault record retention baseline differs")
            faults[identifier] = dict(kind=event["fault_type"], start=t, paused=None, reset=None, resumed=False, committed=committed)
        elif kind == "pause":
            require(event.get("fault_id") in faults, "Pause has unknown fault")
            fault = faults[event["fault_id"]]
            require(not fault["resumed"] and fault["paused"] is None, "Duplicate/late pause")
            fault["paused"] = t
        elif kind == "reset":
            identifier = event.get("reset_id")
            require(type(identifier) is str and identifier and identifier not in resets, "Duplicate/missing reset ID")
            require(type(event.get("reset_ok")) is bool, "Reset result must be boolean")
            fault_id = event.get("fault_id")
            if fault_id is not None:
                require(fault_id in faults and not faults[fault_id]["resumed"], "Reset not inside its declared active fault")
                if event["reset_ok"]:
                    require(faults[fault_id]["paused"] is not None, "Recovery reset precedes pause")
                    faults[fault_id]["reset"] = identifier
                else:
                    faults[fault_id]["reset"] = None
            elif not event["reset_ok"]:
                failures.append("reset_failed_outside_fault")
            resets[identifier] = dict(ok=event["reset_ok"], t=t)
        elif kind == "resume":
            require(event.get("fault_id") in faults, "Resume has unknown fault")
            fault = faults[event["fault_id"]]
            require(not fault["resumed"] and fault["paused"] is not None and fault["reset"] is not None, "Resume before paused and verified reset")
            require(event.get("reset_id") == fault["reset"] and event.get("operator_initiated") is True, "Unverified or automatic resume")
            require(event.get("last_committed_sha256") == fault["committed"], "Committed record lost during recovery")
            fault["resumed"] = True
        elif kind == "lock_probe":
            require(event.get("block") == "protected" and type(event.get("block_id")) is str, "Probe outside protected block")
            lock_count += 1
            probed_blocks.add(event["block_id"])
            if event.get("rejected") is not True or event.get("logged") is not True: failures.append("lock_probe_accepted_or_unlogged")
        elif kind == "trial_begin":
            identifier = event.get("reset_id")
            require(identifier in resets and resets[identifier]["ok"] and identifier not in used_resets, "Trial lacks fresh successful reset")
            require(resets[identifier]["t"] > last_trial and not any(not f["resumed"] for f in faults.values()), "Trial begins before a fresh reset/recovery")
            used_resets.add(identifier)
            last_trial = t
            trials += 1
        elif kind in {"exposure", "cue_playback"}:
            if any(not f["resumed"] for f in faults.values()): failures.append("exposure_before_recovery")
            if kind == "cue_playback":
                require(type(event.get("cue_id")) is str and type(event.get("audible")) is bool and type(event.get("treated_as_unheard")) is bool, "Cue audit fields missing")
                if event["cue_id"] in audible and event["treated_as_unheard"]: failures.append("audible_cue_replayed_as_unheard")
                if event["audible"]: audible.add(event["cue_id"])
        else:
            require(kind in {"session_start", "session_end"}, "Unknown normalized event")
    require(heartbeats and heartbeats[0]["t_s"]-start <= 1 and end-heartbeats[-1]["t_s"] <= 1, "Receiver boundary coverage missing")
    require(all(b["t_s"]-a["t_s"] <= 1 for a,b in zip(heartbeats,heartbeats[1:])), "Receiver logging gap exceeds one second")
    require(heartbeats[-1]["mirrored_frames"] > heartbeats[0]["mirrored_frames"], "No mirrored frame progression")
    require(protected_blocks and trials and resets, "Missing protected/trial/reset coverage")
    if not protected_blocks <= probed_blocks: failures.append("unprobed_protected_block")
    if {f["kind"] for f in faults.values()} != FAULT_TYPES: failures.append("missing_fault_type")
    if any(not f["resumed"] for f in faults.values()): failures.append("incomplete_fault_recovery")
    return dict(duration_s=end-start, stale_gaps_over_250ms=stale, freezes_over_250ms=freezes,
                resets=len(resets), trials=trials, lock_probes=lock_count, faults=len(faults),
                failures=sorted(set(failures)))


def analyze_resources(rows):
    require(len(rows) >= 2, "Resource samples missing")
    for row in rows:
        require(all(finite(row.get(k)) for k in ("t_s", "ram_mb", "ram_capacity_mb", "vram_mb", "vram_capacity_mb", "step_ms")), "Invalid resource sample")
    times = [r["t_s"] for r in rows]
    require(times[-1]-times[0] >= REQUIRED_SECONDS-1 and all(0 < b-a <= 60 for a,b in zip(times,times[1:])), "Incomplete host resource coverage")
    x = [t-times[0] for t in times]; mean_x = sum(x)/len(x)
    denominator = sum((v-mean_x)**2 for v in x)
    result = {}
    for name in ("ram", "vram"):
        capacity = rows[0][name+"_capacity_mb"]
        require(capacity > 0 and all(r[name+"_capacity_mb"] == capacity and r[name+"_mb"] <= capacity for r in rows), "Changing or exceeded resource capacity")
        values = [r[name+"_mb"] for r in rows]; mean_y=sum(values)/len(values)
        slope = sum((a-mean_x)*(b-mean_y) for a,b in zip(x,values))/denominator
        projected = max(values)+max(0,slope)*max(0,10*3600-x[-1])
        result[name] = dict(slope_mb_per_hour=slope*3600, peak_mb=max(values), capacity_mb=capacity,
                            projected_10h_mb=projected, projected_exhaustion=projected >= capacity)
    result["step_ms_max"] = max(r["step_ms"] for r in rows)
    return result


def analyze_manifest(manifest_path):
    path = Path(manifest_path); manifest = load_json(path.read_text(encoding="utf-8"))
    require(type(manifest.get("version")) is int and manifest["version"] == 1 and manifest.get("run_kind") == "actual_soak" and manifest.get("source_kind") == "live", "Actual live soak evidence required")
    require(manifest.get("schedule_kind") == "synthetic_nonstudy" and manifest.get("participants") is False, "Only a non-study synthetic schedule without participants is allowed")
    expected = manifest.get("expected_station_ids")
    require(isinstance(expected,list) and expected and all(type(x) is str and x for x in expected) and len(set(expected)) == len(expected), "Explicit unique station inventory required")
    stations = manifest.get("stations")
    require(isinstance(stations,list) and len(stations) == len(expected) and {s.get("station_id") for s in stations} == set(expected), "Missing or duplicate station evidence")
    require(type(manifest.get('coordinator_clock_id')) is str and bool(manifest['coordinator_clock_id']), 'Common coordinator clock missing')
    require(all(finite(s.get('coordinator_start_s')) and finite(s.get('coordinator_end_s')) for s in stations), 'Station coordinator coverage missing')
    require(min(s['coordinator_end_s'] for s in stations)-max(s['coordinator_start_s'] for s in stations) >= REQUIRED_SECONDS, 'Stations lack a common eight-hour window')
    reports = []
    for station in stations:
        require(station.get("client_kind") in {"headset", "headset_equivalent"}, "Actual receiver client kind missing")
        if station["client_kind"] == "headset_equivalent": require(bool(station.get("substitute_justification", "").strip()), "Substitute client justification required")
        for key in ("scene_sha256", "snapshot_sha256", "receiver_detector_source_sha256"):
            require(SHA.fullmatch(station.get(key,"")), "Missing apparatus/detector binding")
        references = station.get("source_logs")
        require(isinstance(references,dict) and set(references) == {"unity", "publisher", "command", "host"}, "All four native source-log references required")
        require(len({ref.get('path') for ref in references.values()}) == 4, 'Native source roles must retain distinct logs')
        for reference in references.values(): bound_file(path.parent,reference)
        _, content = bound_file(path.parent,station["events"])
        report = analyze_events([load_json(line) for line in content.decode("utf-8").splitlines() if line.strip()])
        _, content = bound_file(path.parent,station["resources"])
        report["resources"] = analyze_resources([{k:float(v) for k,v in row.items()} for row in csv.DictReader(content.decode("utf-8").splitlines())])
        if station["client_kind"] == "headset": bound_file(path.parent,station["battery_thermal_log"])
        if any(report["resources"][name]["projected_exhaustion"] for name in ("ram","vram")): report["failures"].append("projected_memory_exhaustion")
        report.update(station_id=station["station_id"],client_kind=station["client_kind"],scene_sha256=station["scene_sha256"],snapshot_sha256=station["snapshot_sha256"])
        reports.append(report)
    return dict(analysis_complete=True, recommendation="CANDIDATE_GO" if all(not x["failures"] for x in reports) else "NO_GO",
                g2_signed=False, stations=reports, manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                limitations=["Declared provenance and retained hashes are checked; native-log normalization and detector implementation require review",
                             "Memory extrapolation is an engineering screen, not a guaranteed leak bound",
                             "Protected frame freezes are conservatively treated as failures in addition to issue58 stale-state criteria",
                             "This analyzer never authorizes fault injection, station power/network changes, exposure or G2 sign-off"])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest",type=Path); parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    try: result=analyze_manifest(args.manifest)
    except (ValueError,KeyError,TypeError,OSError,UnicodeError) as error:
        result=dict(analysis_complete=False,recommendation="NO_GO",g2_signed=False,reasons=[str(error)])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("x",encoding="utf-8") as stream: json.dump(result,stream,indent=2,allow_nan=False);stream.write("\n")
    return 0 if result["recommendation"] == "CANDIDATE_GO" else 1


if __name__ == "__main__": raise SystemExit(main())
