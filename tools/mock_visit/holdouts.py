"""Scan retained SIMULATION_TEST audio histories for premature held-out phrases.

This verifies recorded request/callback identities, not acoustic delivery or
whether an operator omitted a run. Never publish the private input plan.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path

from . import content
from .records import EvidenceError, exact, guid, hash_value, require, sha, strict, validate_data_payload
from .reconcile import export_bundle, no_links, pinned, read, reconcile, relative, schedule_items

VISITS = {"A": ("D0", "D7"), "B": ("V1", "V2", "V3", "W1", "W4")}


@dataclass(frozen=True)
class VisitEvidence:
    """Internal, already hash/chain-verified input; not a serialized authority."""
    visit: str
    identity: dict
    schedule_sha256: str
    items: dict
    records: list
    messages: dict
    known_pcm: frozenset
    software_complete: bool
    process_started_utc: str
    process_ended_utc: str


def phrase_index(messages):
    require(isinstance(messages, dict) and len(messages) == 32, "HOLDOUT_MESSAGE_REGISTRY")
    result = {}
    for mid, message in messages.items():
        require(message.get("message_id") == mid and message.get("status") in {"trained", "heldout"}, "HOLDOUT_MESSAGE_REGISTRY")
        if message["status"] != "heldout":
            continue
        combinations = message.get("combinations", [message])
        require(isinstance(combinations, list) and 1 <= len(combinations) <= 48, "HOLDOUT_COMBINATIONS")
        for combination in combinations:
            digest = combination.get("composite_sha256")
            require(hash_value(digest), "HOLDOUT_COMPOSITE_HASH")
            # A collision cannot be resolved by the declared audio label.
            require(digest not in result or result[digest] == mid, "HOLDOUT_AMBIGUOUS_PCM")
            result[digest] = mid
    require(result, "HOLDOUT_EMPTY_REGISTRY")
    return result


def scan_visits(study, visits):
    """Conservative request-based scan after normal per-run reconciliation.

Missing callbacks never erase a durable request. Process UTC orders visits; it
is not subtracted from audio clocks. Monotonic values remain within their epoch.
"""
    require(study in VISITS and len(visits) <= 64, "HOLDOUT_HISTORY_LIMIT")
    reasons, complete_visits = set(), set()
    person = index = last_end = last_visit = None
    sessions, events, requests, epochs, engine_epochs, consumed = set(), set(), set(), set(), set(), set()
    owners = {}
    total_records = 0
    counts = dict(audio_requests=0, audio_observations=0, grammar_audio_events=0,
                  authorized_heldout_requests=0, heldout_callback_events=0)
    for visit in visits:
        require(visit.visit in VISITS[study], "HOLDOUT_VISIT")
        order = VISITS[study].index(visit.visit)
        require(last_visit is None or order >= last_visit, "HOLDOUT_VISIT_ORDER")
        last_visit = order
        identity = visit.identity
        total_records += len(visit.records)
        require(total_records <= 500000 and type(visit.software_complete) is bool, "HOLDOUT_HISTORY_LIMIT")
        current_person = identity["coded_id"]
        require(person is None or person == current_person, "HOLDOUT_PERSON_GRAFT")
        person = current_person
        require(identity["visit_id"] == visit.visit and identity["session_id"] not in sessions, "HOLDOUT_SESSION_REUSE")
        sessions.add(identity["session_id"])
        current_index = phrase_index(visit.messages)
        require(index is None or current_index == index, "HOLDOUT_REGISTRY_CHANGED")
        index = current_index
        start, end = (datetime.fromisoformat(s.replace("Z", "+00:00")) for s in (visit.process_started_utc, visit.process_ended_utc))
        require(start.utcoffset() is not None and start.utcoffset().total_seconds() == 0
                and end.utcoffset() is not None and end.utcoffset().total_seconds() == 0
                and start < end and (last_end is None or start >= last_end), "HOLDOUT_PROCESS_ORDER")
        last_end = end
        if not visit.software_complete:
            reasons.add("NATIVE_RUN_INCOMPLETE")
        else:
            require(visit.visit not in complete_visits, "HOLDOUT_DUPLICATE_COMPLETE_VISIT")
            complete_visits.add(visit.visit)
        local_epochs = {r["clock_epoch"] for r in visit.records}
        require(not epochs.intersection(local_epochs), "HOLDOUT_CLOCK_EPOCH_GRAFT")
        epochs.update(local_epochs)
        local_engine_epochs = {r["payload"]["clock_epoch"] for r in visit.records if r["event_type"] == "session"}
        require(not engine_epochs.intersection(local_engine_epochs), "HOLDOUT_ENGINE_EPOCH_GRAFT")
        engine_epochs.update(local_engine_epochs)
        if len(local_epochs) != 1 or any(r["event_type"] == "recovery" for r in visit.records):
            reasons.add("RECOVERED_OR_MISSING_PROCESS_HISTORY")
        cues, permits, used_permits, audio, grammar_intents = {}, {}, set(), {}, set()
        for row in visit.records:
            require(row["identity"] == identity, "HOLDOUT_IDENTITY_GRAFT")
            require(guid(row["event_id"]) and row["event_id"] not in events, "HOLDOUT_EVENT_REUSE")
            events.add(row["event_id"])
            validate_data_payload(row, visit.schedule_sha256)
            p, kind = row["payload"], row["event_type"]
            if kind == "session":
                attempt = p["trial_id"]
                if p["event"] == "state_after" and p["state"] == "CueRequested":
                    require(attempt not in cues and p["opportunity_id"] in visit.items, "HOLDOUT_CUE_REUSE")
                    cues[attempt] = p
                elif p["event"] == "novel_buffer_authorized":
                    require(attempt in cues and attempt not in permits, "HOLDOUT_PERMIT_REUSE_OR_ORDER")
                    cue = cues[attempt]
                    item = visit.items[p["opportunity_id"]]
                    require(item["block"] == "novel" and item.get("message_id") in visit.messages
                            and visit.messages[item["message_id"]]["status"] == "heldout"
                            and all(p[k] == cue[k] for k in ("clock_epoch", "opportunity_id", "audio_request_ids"))
                            and len(p["audio_request_ids"]) == 1 and guid(p["audio_request_ids"][0]), "HOLDOUT_PERMIT_BINDING")
                    permits[attempt] = (item["message_id"], p["audio_request_ids"][0])
                continue
            if kind == "grammar_stage":
                if p["kind"] not in {"request", "audio", "completed"}:
                    continue
                request = p["audio_request_id"]
                binding = (identity["session_id"], "grammar", p["phase"])
                prior = owners.get(request)
                require(prior is None or prior[:3] == binding, "HOLDOUT_GRAMMAR_REQUEST_GRAFT")
                if p["kind"] == "request":
                    require(request not in grammar_intents and prior is None, "HOLDOUT_GRAMMAR_REQUEST_REUSE")
                    grammar_intents.add(request)
                if p["kind"] == "audio":
                    counts["grammar_audio_events"] += 1
                    digest, waveform = p["audio"]["pcm_sha256"], p["audio"]["waveform_sha256"]
                    require(digest in visit.known_pcm, "HOLDOUT_UNINDEXED_AUDIO")
                    require(digest not in index, "HOLDOUT_GRAMMAR_CONTAMINATION")
                    require(prior is None or prior[3] is None or prior[3:] == (digest, waveform), "HOLDOUT_GRAMMAR_PCM_CHANGED")
                    owners[request] = (*binding, digest, waveform)
                elif prior is None:
                    owners[request] = (*binding, None, None)
                continue
            if kind not in {"audio_request", "audio_observation"}:
                continue
            request, digest = row["audio_request_id"], p["pcm_sha256"]
            require(digest in visit.known_pcm, "HOLDOUT_UNINDEXED_AUDIO")
            if kind == "audio_request":
                require(request not in owners and request not in requests, "HOLDOUT_REQUEST_REUSE")
                requests.add(request); counts["audio_requests"] += 1
                owners[request] = (identity["session_id"], "study", row["opportunity_id"], digest, p["waveform_sha256"])
                audio[request] = (row["attempt_id"], row["opportunity_id"], digest)
                if digest not in index:
                    continue
                mid, attempt = index[digest], row["attempt_id"]
                item = visit.items.get(row["opportunity_id"])
                require(item is not None and item["block"] == "novel" and item.get("message_id") == mid,
                        "HOLDOUT_EARLY_OR_WRONG_PHRASE")
                require(permits.get(attempt) == (mid, request) and attempt not in used_permits,
                        "HOLDOUT_PERMIT_MISSING_OR_REUSED")
                require(mid not in consumed, "HOLDOUT_CONSUMED_PHRASE_REPLAY")
                used_permits.add(attempt); consumed.add(mid); counts["authorized_heldout_requests"] += 1
            else:
                counts["audio_observations"] += 1
                require(audio.get(request) == (row["attempt_id"], row["opportunity_id"], digest), "HOLDOUT_OBSERVATION_GRAFT")
                if digest in index and p["callback_count"] > 0:
                    counts["heldout_callback_events"] += 1
        if set(permits) != used_permits:
            reasons.add("PERMIT_WITHOUT_RECORDED_REQUEST")
        expected_novel = {item["message_id"] for item in visit.items.values() if item["block"] == "novel"}
        used_novel = {permits[attempt][0] for attempt in used_permits}
        if used_novel != expected_novel:
            reasons.add("SCHEDULED_NOVEL_REQUESTS_MISSING")
    missing = [v for v in VISITS[study] if v not in complete_visits]
    if missing:
        reasons.add("COMPLETE_VISIT_HISTORY_MISSING")
    scanned = counts["audio_requests"] + counts["grammar_audio_events"] > 0
    if not scanned:
        reasons.add("NO_RETAINED_AUDIO_RECORDS")
    return dict(scope="SIMULATION_TEST", study=study, recorded_history_scan_passed=scanned,
                software_history_complete=not reasons, incomplete_reasons=sorted(reasons),
                visits_present=len(visits), missing_complete_visits=missing, **counts,
                acoustic_qualified=False, omitted_run_custody_verified=False,
                participant_qualified=False, issue78_accepted=False)


def known_audio(base, config, artifacts, atoms, messages):
    """All package combinations, plus byte-verified nonsemantic/speech assets."""
    hashes = {a["pcm_sha256"] for a in atoms.values()}
    for message in messages.values():
        hashes.update(c["composite_sha256"] for c in message.get("combinations", [message]))
    registry_pin = config["files"].get("reserved_registry")
    if registry_pin is not None:
        registry = strict(pinned(base, registry_pin))
        for identifier, directory in ([("ready-cue", "grammar"), ("click-grammar-demo", "grammar")]
                + [("calibration-"+p, "menu_examples") for p in ("P1", "P2", "P3")]):
            if config["directories"].get(directory) is None:
                continue
            matches = [r for r in registry["entries"] if r.get("id") == identifier]
            if not matches:
                continue
            require(len(matches) == 1, "HOLDOUT_RESERVED_AMBIGUOUS")
            entry = matches[0]
            raw = read(base/relative(config["directories"][directory])/(identifier+".wav"), entry["file_sha256"])
            pcm = content.pcm(raw)
            require(sha(pcm) == entry["pcm_sha256"] and len(pcm)//2 == entry["n_samples"], "HOLDOUT_RESERVED_PCM")
            hashes.add(entry["pcm_sha256"])
    for manifest, manifest_base in artifacts:
        for entry in manifest["items"]:
            raw = read(manifest_base/relative(entry["speech_id"]+".wav"), entry["sha256"])
            pcm = content.pcm(raw)
            require(sha(pcm) == entry["pcm_sha256"] and len(pcm)//2 == entry["samples"], "HOLDOUT_SPEECH_PCM")
            hashes.add(entry["pcm_sha256"])
    return frozenset(hashes)


def verify_history(path, expected):
    path = Path(path).absolute(); root = path.parent
    plan = strict(read(path, expected, 1024**2))
    exact(plan, "version scope study role coded_id unit_id package_sha256 runs")
    require(type(plan["version"]) is int and plan["version"] == 1 and plan["scope"] == "SIMULATION_TEST"
            and plan["study"] in VISITS and plan["role"] in ({"reference"} if plan["study"] == "A" else {"active", "yoked"})
            and hash_value(plan["package_sha256"]) and all(isinstance(plan[k], str) and 0 < len(plan[k]) <= 96 for k in ("coded_id", "unit_id")), "HOLDOUT_PLAN")
    require(isinstance(plan["runs"], list) and len(plan["runs"]) <= 64, "HOLDOUT_HISTORY_LIMIT")
    visits, seen, reports = [], set(), []
    missing = False
    for pin in plan["runs"]:
        exact(pin, "path sha256"); require(hash_value(pin["sha256"]) and pin["sha256"] not in seen, "HOLDOUT_RUN_REUSE")
        seen.add(pin["sha256"])
        run = root / relative(pin["path"]); no_links(run)
        if not run.exists():
            missing = True; continue
        m = strict(read(run, pin["sha256"])); base = run.parent
        require(m["study"] == plan["study"] and m["role"] == plan["role"] and m["package_sha256"] == plan["package_sha256"], "HOLDOUT_RUN_BINDING")
        report = reconcile(run, pin["sha256"])
        config = strict(pinned(base, m["config"])); identity = config["identity"]
        schedule = strict(pinned(base, m["schedule"]))
        require(identity["coded_id"] == plan["coded_id"] == schedule.get("person_id")
                and identity["unit_id"] == plan["unit_id"] == schedule.get("unit_id")
                and identity["visit_id"] == m["visit"], "HOLDOUT_PERSON_GRAFT")
        if plan["study"] == "B":
            allocation = strict(pinned((base/m["config"]["path"]).parent, config["files"]["menu_allocation"]))
            members = [member for unit in allocation["dyads"] if unit["unit_id"] == plan["unit_id"]
                       for member in unit["members"] if member["slot_id"] == plan["coded_id"]]
            require(len(members) == 1 and members[0]["role"] == plan["role"], "HOLDOUT_ROLE_GRAFT")
        artifacts = {kind: [a for a in m["artifacts"] if a["kind"] == kind]
                     for kind in ("export_manifest", "package_manifest", "process_result")}
        require(all(len(rows) == 1 for rows in artifacts.values()), "HOLDOUT_ARTIFACT_REQUIRED")
        e = artifacts["export_manifest"][0]
        em, records, _, _ = export_bundle(base/relative(e["path"]), e["sha256"])
        _, atoms, messages = content.package(base, artifacts["package_manifest"][0], plan["package_sha256"], read, relative)
        speech = []
        for entry in m["artifacts"]:
            if entry["kind"] == "speech_manifest":
                speech.append((strict(read(base/relative(entry["path"]), entry["sha256"])), (base/relative(entry["path"])).parent))
        known = known_audio((base/m["config"]["path"]).parent, config, speech, atoms, messages)
        process = strict(pinned(base, {k: artifacts["process_result"][0][k] for k in ("path", "sha256")}))
        visits.append(VisitEvidence(m["visit"], em["identity"], m["schedule"]["sha256"],
                     schedule_items(schedule, m["study"], m["visit"]), records, messages, known,
                     report["software_reconciliation_complete"], process["started_utc"], process["ended_utc"]))
        reports.append(dict(manifest_sha256=pin["sha256"], software_reconciliation_complete=report["software_reconciliation_complete"]))
    result = scan_visits(plan["study"], visits)
    if missing:
        result["software_history_complete"] = False
        result["incomplete_reasons"] = sorted(set(result["incomplete_reasons"]) | {"PINNED_RUN_MISSING"})
    result.update(plan_sha256=expected, role=plan["role"], runs=reports)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan", type=Path, required=True); p.add_argument("--sha256", required=True)
    p.add_argument("--out", type=Path, required=True); args = p.parse_args()
    try:
        result = verify_history(args.plan, args.sha256); no_links(args.out)
        with args.out.open("xb") as stream:
            stream.write((json.dumps(result, indent=2, allow_nan=False)+"\n").encode())
            stream.flush(); os.fsync(stream.fileno())
        print("HOLDOUT_HISTORY " + ("COMPLETE_SOFTWARE_ONLY" if result["software_history_complete"] else "INCOMPLETE"))
        return 0 if result["software_history_complete"] else 3
    except EvidenceError as error:
        print(str(error)); return 2
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        print("HOLDOUT_INPUT_UNREADABLE_OR_MALFORMED"); return 2


if __name__ == "__main__":
    raise SystemExit(main())
