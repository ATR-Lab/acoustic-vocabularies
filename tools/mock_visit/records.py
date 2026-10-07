"""Verify the exact bytes of native journals without reformatting floating values.

Newtonsoft and Python need not spell the same double identically. Hash input is
therefore reconstructed by removing only the top-level hash member from the
original compact JSON; values, escapes, member order and numeric lexemes survive.
"""
from __future__ import annotations

import hashlib
import json
import math
import re


class EvidenceError(ValueError):
    pass


def require(value, code):
    if not value:
        raise EvidenceError(code)


def exact(value, fields, code="MOCK_FIELDS"):
    require(isinstance(value, dict) and set(value) == set(fields.split()), code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def hash_value(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def guid(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 2**63-1
    except OverflowError:
        return False


def strict(raw):
    def pairs(rows):
        obj = {}
        for key, value in rows:
            require(key not in obj, "MOCK_DUPLICATE_JSON_KEY")
            obj[key] = value
        return obj
    def constant(_):
        raise EvidenceError("MOCK_NONFINITE_JSON")
    try:
        text = raw.decode("utf-8", errors="strict") if isinstance(raw, bytes) else raw
        require(not text.startswith("\ufeff"), "MOCK_JSON_BOM")
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
        def bounded(node, level=0):
            require(level <= 32, "MOCK_JSON_DEPTH")
            if isinstance(node, dict):
                for child in node.values(): bounded(child, level + 1)
            elif isinstance(node, list):
                for child in node: bounded(child, level + 1)
            elif isinstance(node, float):
                require(math.isfinite(node), "MOCK_NONFINITE_JSON")
        bounded(value)
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise EvidenceError("MOCK_JSON") from error


def unhashed_bytes(line, *, newline):
    """Remove exactly the unique top-level sha256 property; retain native bytes."""
    require(line.endswith(b"\n") and not line.endswith(b"\r\n"), "MOCK_JOURNAL_TERMINATOR")
    body = line[:-1]
    require(body.startswith(b"{") and body.endswith(b"}"), "MOCK_JOURNAL_OBJECT")
    chunks, start, depth, quoted, escaped = [], 1, 0, False, False
    for index, char in enumerate(body[1:-1], 1):
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
        elif char == 34:
            quoted = True
        elif char in (123, 91):
            depth += 1
        elif char in (125, 93):
            depth -= 1
            require(depth >= 0, "MOCK_JSON_DEPTH")
        elif char == 44 and depth == 0:
            chunks.append(body[start:index]); start = index + 1
        else:
            require(char not in (9, 10, 13, 32), "MOCK_JOURNAL_NONCOMPACT")
    require(not quoted and depth == 0, "MOCK_JSON_DEPTH")
    chunks.append(body[start:-1])
    kept, removed = [], 0
    for chunk in chunks:
        value = strict(b"{" + chunk + b"}")
        require(len(value) == 1, "MOCK_JOURNAL_MEMBER")
        if next(iter(value)) == "sha256":
            removed += 1
        else:
            kept.append(chunk)
    require(removed == 1, "MOCK_JOURNAL_HASH_MEMBER")
    return b"{" + b",".join(kept) + b"}" + (b"\n" if newline else b"")


DATA_FIELDS = "schema_version sequence event_id clock_epoch host_mono_ms identity event_type opportunity_id attempt_id audio_request_id previous_sha256 payload sha256"
IDENTITY_FIELDS = "session_id coded_id visit_id station_id protocol_version build_sha256"
SESSION_FIELDS = "event clock_epoch schedule_sha256 trial_id retry_of block_index item_index host_mono_ms scheduled_onset_mono_ms state audible_status exposure_consumed reset_ok focus_ok technical_fault_code response_code evidence_sha256 opportunity_id audio_request_ids"
AUDIO_FIELDS = "code audio_id waveform_sha256 pcm_sha256 action_pcm_sha256 referent_pcm_sha256 observed_mono_ms request_mono_ms scheduled_mono_ms scheduled_dsp_s onset_estimate_mono_ms onset_uncertainty_ms first_callback_dsp_s delivered_samples callback_count"
SIMULATION_AUDIO_FIELDS = AUDIO_FIELDS + " simulation_test software_output_estimate_mono_ms software_output_uncertainty_ms"
PAYLOADS = {
    "session": SESSION_FIELDS,
    "opportunity": "role method_masked schedule_sha256",
    "panel_process": "kind observed_mono_ms mode role input selected_target selected_action response_code response_target response_action",
    "panel_response": "kind observed_mono_ms mode role input selected_target selected_action response_code response_target response_action",
    "device": "kind observed_mono_ms value duration_ms code",
    "gain": "old_gain new_gain observed_mono_ms reason",
    "choice": "candidate_id accepted_or_rejected yoked_source_event_id pause_ms matching_deviation_id",
    "deviation_reference": "deviation_id signed_log_sha256 observed_mono_ms",
    "visit_exit": "code",
    "recovery": "preserved_tails",
    "assessment_stage": "event_kind schedule_sha256 host_mono_ms clock_epoch stage item_id value outcome_code",
}


def verify_chain(files, kind, *, expected_identity=None):
    """Validate native chains, including exact acknowledged DataJournal tails."""
    require(kind in {"data", "joined", "menu", "operator", "display"}, "MOCK_CHAIN_KIND")
    result, previous, clocks, ids, pending = [], "0" * 64, {}, set(), []
    for name, raw in files:
        require(len(raw) <= 32 * 1024**2, "MOCK_JOURNAL_LIMIT")
        lines = raw.splitlines(keepends=True)
        tail = lines.pop() if lines and not lines[-1].endswith(b"\n") else None
        require(kind == "data" or tail is None, "MOCK_TORN_JOURNAL")
        for line in lines:
            require(1 < len(line) <= 65536 and len(result) < 500000, "MOCK_JOURNAL_LIMIT")
            row = strict(line)
            if kind == "data":
                exact(row, DATA_FIELDS)
                require(row["schema_version"] == "data-events-provisional-1", "MOCK_DATA_VERSION")
                exact(row["identity"], IDENTITY_FIELDS)
                require(all(isinstance(v, str) and 0 < len(v) <= 96 for v in row["identity"].values()) and hash_value(row["identity"]["build_sha256"]), "MOCK_DATA_IDENTITY")
                require(expected_identity is None or row["identity"] == expected_identity, "MOCK_DATA_IDENTITY")
                if pending:
                    require(row["event_type"] == "recovery" and row["payload"] == {"preserved_tails": pending}, "MOCK_RECOVERY_ACK")
                    pending = []
                elif row["event_type"] == "recovery":
                    require(row["payload"] == {"preserved_tails": []}, "MOCK_RECOVERY_ACK")
                require(guid(row["event_id"]) and row["event_id"] not in ids, "MOCK_EVENT_ID")
                ids.add(row["event_id"])
                epoch, stamp = row["clock_epoch"], row["host_mono_ms"]
                require(guid(epoch), "MOCK_CLOCK_EPOCH")
            elif kind in {"joined", "display"}:
                exact(row, "version clock_epoch sequence host_mono_ms previous_sha256 kind payload sha256")
                require(type(row["version"]) is int and row["version"] == 1, "MOCK_JOURNAL_VERSION")
                epoch, stamp = row["clock_epoch"], row["host_mono_ms"]
                require(guid(epoch), "MOCK_CLOCK_EPOCH")
            elif kind == "menu":
                exact(row, "version sequence previous_sha256 record sha256")
                require(type(row["version"]) is int and row["version"] == 1, "MOCK_JOURNAL_VERSION")
                epoch, stamp = "menu", row["record"].get("mono_ms", clocks.get("menu", 0))
            else:
                exact(row, "version session_nonce sequence previous_sha256 record sha256")
                require(type(row["version"]) is int and row["version"] == 1 and guid(row["session_nonce"]), "MOCK_JOURNAL_VERSION")
                epoch, stamp = row["session_nonce"], row["record"].get("host_mono_ms")
            require(type(row["sequence"]) is int and row["sequence"] == len(result) and row["previous_sha256"] == previous, "MOCK_CHAIN_SEQUENCE")
            require(hash_value(row["sha256"]) and sha(unhashed_bytes(line, newline=kind == "data")) == row["sha256"], "MOCK_CHAIN_HASH")
            require(number(stamp) and stamp >= clocks.get(epoch, 0), "MOCK_CLOCK_REGRESSION")
            clocks[epoch] = stamp
            previous = row["sha256"]; result.append(row)
        if tail is not None:
            pending.append({"segment": name.rsplit("/", 1)[-1], "tail_offset": len(raw) - len(tail), "tail_sha256": sha(tail), "segment_sha256": sha(raw)})
    require(not pending, "MOCK_UNACKNOWLEDGED_TORN_TAIL")
    return result


def validate_audio(p):
    require(isinstance(p, dict), "MOCK_AUDIO_PAYLOAD")
    simulation = "simulation_test" in p
    exact(p, SIMULATION_AUDIO_FIELDS if simulation else AUDIO_FIELDS)
    require(hash_value(p["pcm_sha256"]), "MOCK_PCM_HASH")
    require(all(p[k] is None or hash_value(p[k]) for k in ("waveform_sha256", "action_pcm_sha256", "referent_pcm_sha256")), "MOCK_AUDIO_HASH")
    require(all(number(p[k]) for k in ("observed_mono_ms", "request_mono_ms", "scheduled_mono_ms", "scheduled_dsp_s")), "MOCK_AUDIO_TIME")
    require(p["first_callback_dsp_s"] is None or number(p["first_callback_dsp_s"]), "MOCK_AUDIO_TIME")
    require(all(type(p[k]) is int and p[k] >= 0 for k in ("delivered_samples", "callback_count")), "MOCK_AUDIO_COUNT")
    require(p["onset_estimate_mono_ms"] is None and p["onset_uncertainty_ms"] is None, "MOCK_ACOUSTIC_AUTHORITY_FORBIDDEN")
    if simulation:
        require(p["simulation_test"] is True and all(number(p[k]) for k in ("software_output_estimate_mono_ms", "software_output_uncertainty_ms")), "MOCK_SOFTWARE_OUTPUT_TIME")
        require(p["software_output_uncertainty_ms"] <= 20, "MOCK_SOFTWARE_UNCERTAINTY_LIMIT")
    return simulation


def validate_data_payload(row, schedule_hash):
    kind, p = row["event_type"], row["payload"]
    require(isinstance(p, dict), "MOCK_PAYLOAD")
    if kind in {"audio_request", "audio_observation"}:
        validate_audio(p)
        require(guid(row["audio_request_id"]) and row["attempt_id"] and row["opportunity_id"], "MOCK_AUDIO_CONTEXT")
        require(hash_value(p["pcm_sha256"]), "MOCK_PCM_HASH")
        require(all(p[k] is None or hash_value(p[k]) for k in ("waveform_sha256", "action_pcm_sha256", "referent_pcm_sha256")), "MOCK_AUDIO_HASH")
        require(all(number(p[k]) for k in ("observed_mono_ms", "request_mono_ms", "scheduled_mono_ms", "scheduled_dsp_s")), "MOCK_AUDIO_TIME")
        require(all(type(p[k]) is int and p[k] >= 0 for k in ("delivered_samples", "callback_count")), "MOCK_AUDIO_COUNT")
        # This verifier is intentionally simulation-only. Acoustic status cannot
        # be inferred from actual output callback coverage or process success.
        require(p["onset_estimate_mono_ms"] is None and p["onset_uncertainty_ms"] is None, "MOCK_ACOUSTIC_AUTHORITY_FORBIDDEN")
    elif kind == "audio_evidence":
        raise EvidenceError("MOCK_ACOUSTIC_AUTHORITY_FORBIDDEN")
    elif kind in PAYLOADS:
        exact(p, PAYLOADS[kind])
        if kind in {"session", "opportunity", "assessment_stage"}:
            require(p["schedule_sha256"] == schedule_hash, "MOCK_SCHEDULE_BINDING")
        if kind == "session":
            require(p["trial_id"] == row["attempt_id"] and p["opportunity_id"] == row["opportunity_id"] and row["audio_request_id"] is None, "MOCK_SESSION_CONTEXT")
            require(p["audible_status"] in {"NotRequested", "NoCue", "Uncertain"}, "MOCK_ACOUSTIC_AUTHORITY_FORBIDDEN")
            require(type(p["exposure_consumed"]) is bool and p["exposure_consumed"] == (p["audible_status"] == "Uncertain"), "MOCK_EXPOSURE_CONSUMPTION")
    elif kind == "lesson":
        from .lessons import validate
        validate(row)
        require(p["schedule_sha256"] == schedule_hash, "MOCK_SCHEDULE_BINDING")
    elif kind == "grammar_stage":
        exact(p, "version schedule_sha256 registry_sha256 review_sha256 clock_epoch host_mono_ms kind phase audio_request_id operator_command audio")
        require(type(p["version"]) is int and p["version"] == 1 and p["schedule_sha256"] == schedule_hash, "MOCK_GRAMMAR_VERSION")
        require(all(hash_value(p[k]) for k in ("registry_sha256", "review_sha256")) and guid(p["clock_epoch"]) and number(p["host_mono_ms"]), "MOCK_GRAMMAR_BINDING")
        require(p["kind"] in {"started", "request", "audio", "completed", "finished", "interrupted"}, "MOCK_GRAMMAR_KIND")
        play = p["kind"] in {"request", "audio", "completed"}
        require((p["phase"] in {"ready", "clicks"} and guid(p["audio_request_id"])) if play else p["phase"] is None and p["audio_request_id"] is None, "MOCK_GRAMMAR_CONTEXT")
        if p["kind"] == "started":
            exact(p["operator_command"], "session_nonce request_id sequence")
            require(guid(p["operator_command"]["session_nonce"]) and guid(p["operator_command"]["request_id"]) and type(p["operator_command"]["sequence"]) is int and p["operator_command"]["sequence"] > 0, "MOCK_GRAMMAR_COMMAND")
        else:
            require(p["operator_command"] is None, "MOCK_GRAMMAR_COMMAND")
        if p["kind"] == "audio":
            validate_audio(p["audio"])
            require(p["audio"]["audio_id"] == p["audio_request_id"], "MOCK_GRAMMAR_AUDIO")
        else:
            require(p["audio"] is None, "MOCK_GRAMMAR_AUDIO")
        require(row["opportunity_id"] is None and row["attempt_id"] is None and row["audio_request_id"] is None, "MOCK_GRAMMAR_CONTEXT")
    else:
        raise EvidenceError("MOCK_UNKNOWN_DATA_EVENT")
