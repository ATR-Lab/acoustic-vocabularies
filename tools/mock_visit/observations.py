"""Source-exact software observation projection for versioned native exports."""
from .records import exact, guid, hash_value, number, require, strict

KINDS = {"slot_context", "panel_response", "panel_process", "audio_request", "audio_observation",
         "audio_evidence", "lesson", "assessment_stage", "menu_observation", "device", "choice", "session"}
SLOT_FIELDS = ("schema_version schedule_sha256 package_sha256 session_clock_epoch observation_clock_epoch clock_basis "
               "loaded_event_sha256 person_slot trial_type content_id role presentation phase heldout slot_seconds plays pass "
               "scheduled_onset_mono_ms audio_request_ids dictionary_policy feedback_policy")
MENU_FIELDS = ("schema_version schedule_sha256 package_sha256 role event_id kind menu_key meaning_display_id slot_start_mono_ms "
               "observed_mono_ms expected_mono_ms onset_uncertainty_ms presentation_index candidate_id pcm_sha256 file_sha256 "
               "yoked_source_event_id selected_index defaulted phase receipt_sha256 matching_deviation_id")


def validate(row, schedule_hash):
    p, kind = row["payload"], row["event_type"]
    require(row["attempt_id"] is not None and row["opportunity_id"] is not None, "MOCK_NATIVE_CONTEXT")
    exact(p, SLOT_FIELDS if kind == "slot_context" else MENU_FIELDS)
    require(p["schedule_sha256"] == schedule_hash and hash_value(p["package_sha256"]), "MOCK_NATIVE_PINS")
    if kind == "slot_context":
        require(p["schema_version"] == "native-slot-context-1" and guid(p["session_clock_epoch"])
                and guid(p["observation_clock_epoch"]) and p["clock_basis"] == "shared_host_monotonic"
                and hash_value(p["loaded_event_sha256"]), "MOCK_NATIVE_SLOT")
        require(p["phase"] in {"selection", "teaching", "pre_test", "protected", "validity"}
                and type(p["heldout"]) is bool and p["dictionary_policy"] == "prohibited"
                and p["feedback_policy"] == ("scheduled_lesson" if p["phase"] == "teaching" else "prohibited"), "MOCK_NATIVE_POLICY")
        require(type(p["plays"]) is int and 0 <= p["plays"] <= 8 and isinstance(p["audio_request_ids"], list)
                and len(p["audio_request_ids"]) == p["plays"] and all(guid(v) for v in p["audio_request_ids"])
                and len(set(p["audio_request_ids"])) == p["plays"] and number(p["scheduled_onset_mono_ms"]), "MOCK_NATIVE_AUDIO")
    else:
        require(p["schema_version"] == "native-menu-event-1" and p["role"] in {"active", "yoked"}
                and guid(p["event_id"]) and p["kind"] in {"menu_start", "choice_revised", "choice_final", "selection_verified",
                "display_request", "display_changed", "play_request", "onset_authority", "play_complete", "menu_interrupted"}, "MOCK_NATIVE_MENU")
        require(number(p["observed_mono_ms"]) and p["observed_mono_ms"] <= row["host_mono_ms"]
                and number(p["slot_start_mono_ms"]), "MOCK_NATIVE_MENU_TIME")


def verify_projection(raw, records, identity):
    projection = strict(raw)
    exact(projection, "format format_version evidence_level acoustic_authority identity events")
    require(projection["format"] == "av-native-observations" and type(projection["format_version"]) is int
            and projection["format_version"] == 1 and projection["evidence_level"] == "software_event_calls"
            and projection["acoustic_authority"] is False and projection["identity"] == identity, "MOCK_NATIVE_FORMAT")
    expected = [dict(sequence=r["sequence"], source_event_sha256=r["sha256"], clock_epoch=r["clock_epoch"], host_mono_ms=r["host_mono_ms"],
                     opportunity_id=r["opportunity_id"], attempt_id=r["attempt_id"], audio_request_id=r["audio_request_id"], kind=r["event_type"], payload=r["payload"])
                for r in records if r["event_type"] in KINDS]
    require(projection["events"] == expected, "MOCK_NATIVE_PROJECTION")
    prior, slots, loaded_sources = {}, {}, set()
    for r in records:
        p, kind = r["payload"], r["event_type"]
        if kind == "slot_context":
            source = prior.get(p["loaded_event_sha256"])
            require(source is not None and source["event_type"] == "session" and source["attempt_id"] == r["attempt_id"]
                    and source["opportunity_id"] == r["opportunity_id"] and source["payload"]["event"] == "state_after"
                    and source["payload"]["state"] == "Loaded" and source["payload"]["clock_epoch"] == p["session_clock_epoch"]
                    and source["payload"]["audio_request_ids"] == p["audio_request_ids"]
                    and source["payload"]["scheduled_onset_mono_ms"] == p["scheduled_onset_mono_ms"], "MOCK_NATIVE_LOADED")
            require(p["loaded_event_sha256"] not in loaded_sources and p["observation_clock_epoch"] == r["clock_epoch"], "MOCK_NATIVE_SLOT_CHANGED")
            loaded_sources.add(p["loaded_event_sha256"])
            slots[r["attempt_id"]] = p
        elif kind == "menu_observation":
            slot = slots.get(r["attempt_id"])
            require(slot is not None and slot["phase"] == "selection" and slot["package_sha256"] == p["package_sha256"]
                    and slot["schedule_sha256"] == p["schedule_sha256"] and slot["scheduled_onset_mono_ms"] == p["slot_start_mono_ms"], "MOCK_NATIVE_MENU_SLOT")
            if r["audio_request_id"] is not None:
                require(type(p["presentation_index"]) is int and 1 <= p["presentation_index"] <= len(slot["audio_request_ids"])
                        and slot["audio_request_ids"][p["presentation_index"] - 1] == r["audio_request_id"], "MOCK_NATIVE_MENU_AUDIO")
        prior[r["sha256"]] = r
