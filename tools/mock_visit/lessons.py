"""Optional provisional lesson table: software events, not acoustic/scanout proof."""
from __future__ import annotations

import csv
import io
import re
from .records import exact, guid, hash_value, number, require, strict

VERSION = "lesson-exposures-provisional-1"
TABLE, CONTRACT = "lesson-exposures.csv", "lesson-header-contract.json"
EVENT_FIELDS = "kind attempt_id opportunity_id audio_request_id presentation_index observed_mono_ms expected_mono_ms meaning_display_id feedback_content_id highlight pcm_sha256 action_pcm_sha256 referent_pcm_sha256".split()
BINDING_FIELDS = "schema_version schedule_sha256 package_sha256 session_clock_epoch lesson_type slot_start_mono_ms audio_request_ids".split()
COLUMNS = "schema_version session_id coded_id visit_id station_id schedule_sha256 package_sha256 clock_epoch session_clock_epoch opportunity_id attempt_id context_audio_request_ids lesson_type row_kind presentation_index audio_request_id meaning_display_id feedback_content_id pcm_sha256 action_pcm_sha256 referent_pcm_sha256 start_event_sha256 end_event_sha256 observed_start_mono_ms observed_end_mono_ms expected_start_mono_ms display_start_mono_ms display_end_mono_ms retrieval_opportunity interval_status lesson_status audio_ledger_status audible_status exposure_consumed audio_onset_estimate_mono_ms onset_uncertainty_ms evidence_level".split()
KINDS = set("play_request onset_authority play_complete display_request display_start display_end retrieval_opportunity retrieval_result highlight_request highlight lesson_end lesson_interrupted".split())
PLAY = {"play_request", "onset_authority", "play_complete"}


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value) is not None


def validate(row):
    p = row["payload"]
    exact(p, " ".join(BINDING_FIELDS + EVENT_FIELDS))
    require(p["schema_version"] == "lesson-events-provisional-1" and hash_value(p["schedule_sha256"])
            and hash_value(p["package_sha256"]) and guid(p["session_clock_epoch"]), "MOCK_LESSON_BINDING")
    require(p["lesson_type"] in {"atomic_lesson", "message_lesson"} and p["kind"] in KINDS, "MOCK_LESSON_KIND")
    require(all(identifier(p[k]) for k in ("attempt_id", "opportunity_id", "meaning_display_id"))
            and all(row[k] == p[k] for k in ("attempt_id", "opportunity_id", "audio_request_id")), "MOCK_LESSON_CONTEXT")
    require(number(row["host_mono_ms"]) and p["observed_mono_ms"] <= row["host_mono_ms"], "MOCK_LESSON_FUTURE_EVENT")
    ids = p["audio_request_ids"]
    require(isinstance(ids, list) and len(ids) == 3 and all(guid(x) for x in ids) and len(set(ids)) == 3, "MOCK_LESSON_AUDIO_IDS")
    if p["kind"] in PLAY:
        index = p["presentation_index"]
        require(type(index) is int and 1 <= index <= 3 and p["audio_request_id"] == ids[index-1], "MOCK_LESSON_PRESENTATION")
    else:
        require(p["presentation_index"] is None and p["audio_request_id"] is None, "MOCK_LESSON_PRESENTATION")
    require(number(p["slot_start_mono_ms"]) and number(p["observed_mono_ms"])
            and (p["expected_mono_ms"] is None or number(p["expected_mono_ms"])), "MOCK_LESSON_TIME")
    if p["kind"] in {"play_request", "onset_authority", "display_start", "display_end", "retrieval_opportunity", "lesson_end", "lesson_interrupted"}:
        require(number(p["expected_mono_ms"]), "MOCK_LESSON_EXPECTED_TIME")
    require(hash_value(p["pcm_sha256"]) and (p["feedback_content_id"] is None or identifier(p["feedback_content_id"])), "MOCK_LESSON_CONTENT")
    atoms = [p["action_pcm_sha256"], p["referent_pcm_sha256"]]
    require(all(x is None for x in atoms) if p["lesson_type"] == "atomic_lesson" else all(hash_value(x) for x in atoms), "MOCK_LESSON_ATOMS")
    require(p["highlight"] in {"none", "action", "target"} if p["kind"] in {"highlight", "highlight_request"} else p["highlight"] is None, "MOCK_LESSON_HIGHLIGHT")
    if p["kind"] == "retrieval_result": require(p["feedback_content_id"] is not None, "MOCK_LESSON_FEEDBACK")


def groups(records):
    loaded, traces = {}, {}
    for row in records:
        p = row["payload"]
        if row["event_type"] == "session" and p["event"] == "state_after" and p["state"] == "Loaded":
            loaded[row["attempt_id"]] = p
        if row["event_type"] != "lesson": continue
        validate(row)
        s = loaded.get(p["attempt_id"])
        require(s is not None and all(s[k] == p[k] for k in ("schedule_sha256", "opportunity_id", "audio_request_ids"))
                and s["clock_epoch"] == p["session_clock_epoch"] and s["scheduled_onset_mono_ms"] == p["slot_start_mono_ms"]
                and s["host_mono_ms"] <= p["observed_mono_ms"], "MOCK_LESSON_LOADED_BINDING")
        key = (p["session_clock_epoch"], p["audio_request_ids"][0])
        trace = traces.setdefault(key, {"events": [], "once": set(), "plays": 0, "display": None, "retrieval": False, "feedback": None, "ended": False})
        events, once = trace["events"], trace["once"]
        if events:
            first, last = events[0], events[-1]
            require(all(first["payload"][k] == p[k] for k in BINDING_FIELDS + ["attempt_id", "opportunity_id", "meaning_display_id", "pcm_sha256", "action_pcm_sha256", "referent_pcm_sha256"]), "MOCK_LESSON_CHANGED")
            require(row["clock_epoch"] == first["clock_epoch"] and p["observed_mono_ms"] >= last["payload"]["observed_mono_ms"], "MOCK_LESSON_CLOCK")
        kind, index, feedback = p["kind"], p["presentation_index"], p["feedback_content_id"]
        require(not trace["ended"] or kind == "highlight" and p["highlight"] == "none", "MOCK_LESSON_AFTER_END")
        if kind == "play_request":
            trace["plays"] += 1
            require(index == trace["plays"], "MOCK_LESSON_PLAY_ORDER")
        if kind in PLAY:
            require((kind, index) not in once and (kind == "play_request" or ("play_request", index) in once)
                    and (kind != "play_complete" or ("onset_authority", index) in once), "MOCK_LESSON_DUPLICATE_OR_ORDER")
            once.add((kind, index))
        if kind == "display_start":
            key = "definition" if feedback is None else "feedback"
            require(trace["display"] is None and ("display", key) not in once
                    and (feedback is None or trace["retrieval"] and feedback == trace["feedback"]), "MOCK_LESSON_DISPLAY_ORDER")
            trace["display"] = key; once.add(("display", key))
        if kind == "display_end":
            require(trace["display"] == ("definition" if feedback is None else "feedback"), "MOCK_LESSON_DISPLAY_ORDER")
            trace["display"] = None
        if kind == "retrieval_opportunity":
            require(not trace["retrieval"] and trace["display"] is None and ("display", "definition") in once, "MOCK_LESSON_RETRIEVAL_ORDER")
            trace["retrieval"] = True
        if kind == "retrieval_result":
            require(trace["retrieval"] and trace["feedback"] is None, "MOCK_LESSON_RETRIEVAL_ORDER")
            trace["feedback"] = feedback
        if kind in {"lesson_end", "lesson_interrupted"}:
            require(not trace["ended"] and trace["display"] is None, "MOCK_LESSON_END_ORDER")
            trace["ended"] = True
        events.append(row)
    return [t["events"] for t in traces.values()]


def derive(records, identity, exposures):
    audio = {r["audio_request_id"]: r for r in exposures}
    requests = {r["audio_request_id"]: r for r in records if r["event_type"] == "audio_request"}
    result = []
    for events in groups(records):
        status = "interrupted" if any(r["payload"]["kind"] == "lesson_interrupted" for r in events) else "software_ended" if any(r["payload"]["kind"] == "lesson_end" for r in events) else "incomplete"
        for start in events:
            p = start["payload"]
            if p["kind"] not in {"play_request", "display_start", "retrieval_opportunity"}: continue
            kind = {"play_request":"play", "display_start":"display", "retrieval_opportunity":"retrieval"}[p["kind"]]
            end_kind = {"play":"play_complete", "display":"display_end", "retrieval":"retrieval_result"}[kind]
            endings = [r for r in events if r["sequence"] > start["sequence"] and r["payload"]["kind"] == end_kind
                       and (kind != "play" or r["payload"]["presentation_index"] == p["presentation_index"])
                       and (kind != "display" or r["payload"]["feedback_content_id"] == p["feedback_content_id"])]
            require(len(endings) <= 1, "MOCK_LESSON_DUPLICATE_END")
            end = endings[0] if endings else None
            row = dict.fromkeys(COLUMNS, "")
            row.update(schema_version=VERSION, **{k:identity[k] for k in ("session_id", "coded_id", "visit_id", "station_id")})
            row.update({k:p[k] if p[k] is not None else "" for k in ("schedule_sha256", "package_sha256", "session_clock_epoch", "opportunity_id", "attempt_id", "lesson_type", "presentation_index", "audio_request_id", "meaning_display_id", "feedback_content_id", "pcm_sha256", "action_pcm_sha256", "referent_pcm_sha256")})
            row.update(clock_epoch=start["clock_epoch"], context_audio_request_ids=";".join(p["audio_request_ids"]), row_kind=kind,
                start_event_sha256=start["sha256"], end_event_sha256=end["sha256"] if end else "",
                observed_start_mono_ms=p["observed_mono_ms"], observed_end_mono_ms=end["payload"]["observed_mono_ms"] if end else "",
                expected_start_mono_ms=p["expected_mono_ms"] if p["expected_mono_ms"] is not None else "", interval_status="software_observed" if end else "incomplete",
                lesson_status=status, evidence_level="software_event_calls_not_physical_presentation", retrieval_opportunity="true" if kind == "retrieval" else "false")
            if kind == "display": row.update(display_start_mono_ms=row["observed_start_mono_ms"], display_end_mono_ms=row["observed_end_mono_ms"])
            if kind == "retrieval": row["feedback_content_id"] = end["payload"]["feedback_content_id"] if end else ""
            if kind == "play":
                row["audio_ledger_status"] = "request_missing"
                request_id = p["audio_request_id"]
                if request_id in audio:
                    q = requests.get(request_id)
                    require(q is not None and q["sequence"] > start["sequence"] and all(q[k] == p[k] for k in ("attempt_id", "opportunity_id")), "MOCK_LESSON_AUDIO_BINDING")
                    require(all(q["payload"][k] == p[k] for k in ("pcm_sha256", "action_pcm_sha256", "referent_pcm_sha256")), "MOCK_LESSON_AUDIO_HASH")
                    row["audio_ledger_status"] = "linked"
                    row.update({k:audio[request_id][k] for k in ("audible_status", "exposure_consumed", "audio_onset_estimate_mono_ms", "onset_uncertainty_ms")})
            result.append(row)
    return result


def verify_export(files, records, identity, exposures):
    present = any(r["event_type"] == "lesson" for r in records)
    require((TABLE in files) == (CONTRACT in files) == present, "MOCK_LESSON_EXPORT_PAIR")
    if not present: return
    contract = strict(files[CONTRACT])
    exact(contract, "schema_version qualified headers exposure_ledger_relation evidence_level acoustic_authority")
    require(contract == dict(schema_version=VERSION, qualified=False, headers=COLUMNS,
        exposure_ledger_relation="play_rows_join_by_audio_request_id_no_additional_exposures",
        evidence_level="software_event_calls_not_physical_presentation", acoustic_authority=False), "MOCK_LESSON_HEADER_CONTRACT")
    reader = csv.DictReader(io.StringIO(files[TABLE].decode("utf-8")))
    require(reader.fieldnames == COLUMNS, "MOCK_LESSON_CSV_HEADER")
    actual, expected = list(reader), derive(records, identity, exposures)
    require(len(actual) == len(expected), "MOCK_LESSON_CSV_COUNT")
    for row, want in zip(actual, expected):
        require(set(row) == set(COLUMNS), "MOCK_LESSON_CSV_ROW")
        for key in COLUMNS:
            value = want[key]
            if type(value) in (int, float):
                try: match = float(row[key]) == value
                except (ValueError, TypeError): match = False
            else: match = row[key] == value
            require(match, "MOCK_LESSON_CSV_MISMATCH")


def reconcile(records, joined, items, schedule_hash, package_hash):
    """Bind optional typed events to the actual schedule and retained side log."""
    typed = [r for r in records if r["event_type"] == "lesson"]
    if not typed: return set()  # Old exports retain their original interpretation.
    traces = groups(records)
    for events in traces:
        p = events[0]["payload"]; item = items.get(p["opportunity_id"])
        require(item is not None and item["block"] in {"atomic_lessons", "message_lessons"}
                and item["trial_type"] == p["lesson_type"] and p["schedule_sha256"] == schedule_hash
                and p["package_sha256"] == package_hash, "MOCK_LESSON_SCHEDULE")
    expected = [{k:r["payload"][k] for k in EVENT_FIELDS} for r in typed]
    actual = [r["payload"] for r in joined if r["kind"] == "lesson"]
    # The typed write precedes its supplementary write. A failed second sink
    # may leave an honest final prefix; it cannot certify complete coverage.
    require(len(actual) <= len(expected) and actual == expected[:len(actual)], "MOCK_LESSON_SUPPLEMENTAL_MISMATCH")
    incomplete = set()
    if len(actual) != len(expected): incomplete.add("LESSON_SUPPLEMENTAL_WRITE_INCOMPLETE")
    if any(not any(r["payload"]["kind"] == "lesson_end" for r in rows) for rows in traces): incomplete.add("LESSON_TYPED_INTERVALS_INCOMPLETE")
    return incomplete
