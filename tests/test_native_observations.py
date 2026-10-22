"""Source projection checks; these fixtures make no presentation claim."""
import copy
import json

import pytest

from tools.mock_visit.observations import KINDS, validate, verify_projection
from tools.mock_visit.records import EvidenceError


def fixture():
    loaded = dict(sequence=0, sha256="a" * 64, clock_epoch="1" * 32, host_mono_ms=0,
                  opportunity_id="trial", attempt_id="trial", audio_request_id=None, event_type="session",
                  payload=dict(event="state_after", state="Loaded", clock_epoch="2" * 32,
                               audio_request_ids=["3" * 32], scheduled_onset_mono_ms=1000))
    slot = dict(sequence=1, sha256="b" * 64, clock_epoch="1" * 32, host_mono_ms=1,
                opportunity_id="trial", attempt_id="trial", audio_request_id=None, event_type="slot_context",
                payload=dict(schema_version="native-slot-context-1", schedule_sha256="c" * 64, package_sha256="d" * 64,
                             session_clock_epoch="2" * 32, observation_clock_epoch="1" * 32, clock_basis="shared_host_monotonic",
                             loaded_event_sha256="a" * 64, person_slot="A-C01-L01", trial_type="trained", content_id="K-a1-r1",
                             role=None, presentation=None, phase="protected", heldout=False, slot_seconds=14, plays=1, **{"pass": 1},
                             scheduled_onset_mono_ms=1000, audio_request_ids=["3" * 32], dictionary_policy="prohibited", feedback_policy="prohibited"))
    records = [loaded, slot]
    events = [dict(sequence=r["sequence"], source_event_sha256=r["sha256"], clock_epoch=r["clock_epoch"], host_mono_ms=r["host_mono_ms"],
                   opportunity_id=r["opportunity_id"], attempt_id=r["attempt_id"], audio_request_id=r["audio_request_id"], kind=r["event_type"], payload=r["payload"])
              for r in records if r["event_type"] in KINDS]
    return records, dict(format="av-native-observations", format_version=1, evidence_level="software_event_calls", acoustic_authority=False,
                         identity={"coded_id": "DEMO-A-C01-L01"}, events=events)


def test_native_projection_retains_unknowns_and_separate_clock_binding():
    records, projection = fixture()
    validate(records[1], "c" * 64)
    verify_projection(json.dumps(projection), records, projection["identity"])
    assert projection["events"][1]["payload"]["role"] is None


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "payload", "clock", "source"])
def test_native_projection_cannot_rehash_or_reorder_source_facts(mutation):
    records, original = fixture()
    projection = copy.deepcopy(original)
    if mutation == "missing":
        projection["events"].pop()
    elif mutation == "duplicate":
        projection["events"].append(copy.deepcopy(projection["events"][-1]))
    elif mutation == "payload":
        projection["events"][1]["payload"]["content_id"] = "K-a2-r2"
    elif mutation == "clock":
        projection["events"][1]["clock_epoch"] = "4" * 32
    else:
        projection["events"][1]["source_event_sha256"] = "f" * 64
    with pytest.raises(EvidenceError, match="MOCK_NATIVE_PROJECTION"):
        verify_projection(json.dumps(projection), records, original["identity"])


def test_native_slot_rejects_added_fields_and_wrong_loaded_source():
    records, projection = fixture()
    changed = copy.deepcopy(records[1])
    changed["payload"]["hidden_answer"] = "not allowed"
    with pytest.raises(EvidenceError, match="MOCK_FIELDS"):
        validate(changed, "c" * 64)
    records[1]["payload"]["loaded_event_sha256"] = "f" * 64
    with pytest.raises(EvidenceError, match="MOCK_NATIVE_LOADED"):
        verify_projection(json.dumps(projection), records, projection["identity"])
