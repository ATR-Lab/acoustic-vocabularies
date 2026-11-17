"""Scheduled-slot opportunity rows in the public schema and the mock-visit verifier."""
import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from tools.mock_visit.records import EvidenceError, validate_data_payload

ROOT = Path(__file__).resolve().parents[2]
SCHEDULE = "b" * 64


def opportunity_event():
    """The first synthetic event is an older three-field opportunity row."""
    event = json.loads((ROOT / "docs/data/synthetic-visit/events.jsonl").read_text().splitlines()[0])
    assert event["event_type"] == "opportunity" and set(event["payload"]) == {"role", "method_masked", "schedule_sha256"}
    return event


def slot_event():
    event = opportunity_event()
    event["payload"].update({"block": "trained", "pass": 2, "item_kind": "trained"})
    return event


def test_schema_accepts_a_slot_row_and_the_older_row():
    """REQ-LOG-01: a visit-start row carries block, pass and item kind; older rows stay valid."""
    validator = Draft202012Validator(json.loads((ROOT / "apparatus/data/data-event.schema.json").read_text()))
    validator.validate(opportunity_event())
    validator.validate(slot_event())
    for missing in ("block", "pass", "item_kind"):
        partial = slot_event()
        del partial["payload"][missing]
        assert list(validator.iter_errors(partial)), missing
    for key, value in (("pass", 0), ("pass", "2"), ("block", ""), ("item_kind", "two words")):
        bad = slot_event()
        bad["payload"][key] = value
        assert list(validator.iter_errors(bad)), (key, value)


def test_mock_visit_verifier_accepts_a_slot_row_and_refuses_partial_or_malformed_ones():
    """REQ-LOG-01: mock-visit reconciliation reads the visit-start opportunity rows."""
    validate_data_payload(opportunity_event(), SCHEDULE)
    validate_data_payload(slot_event(), SCHEDULE)
    partial = slot_event()
    del partial["payload"]["item_kind"]
    with pytest.raises(EvidenceError, match="MOCK_FIELDS"):
        validate_data_payload(partial, SCHEDULE)
    for key, value in (("pass", 0), ("pass", True), ("block", None), ("item_kind", "two words")):
        bad = copy.deepcopy(slot_event())
        bad["payload"][key] = value
        with pytest.raises(EvidenceError, match="MOCK_OPPORTUNITY_SLOT"):
            validate_data_payload(bad, SCHEDULE)
    with pytest.raises(EvidenceError, match="MOCK_SCHEDULE_BINDING"):
        validate_data_payload(slot_event(), "c" * 64)
