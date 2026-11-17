"""Resume-guard rows (#278) in the public data-event contract and the mock-visit verifier.

Synthetic rows only. The C# producer is covered by the Unity resume-guard tests.
"""
import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from tools.mock_visit.records import EvidenceError, validate_data_payload

ROOT = Path(__file__).resolve().parents[2]
SESSION = "0123456789abcdef0123456789abcdef"


def event(kind, payload, opportunity=None):
    first = json.loads((ROOT / "docs/data/synthetic-visit/events.jsonl").read_text().splitlines()[0])
    return dict(first, event_type=kind, opportunity_id=opportunity, attempt_id=None, audio_request_id=None, payload=payload)


@pytest.fixture(scope="module")
def schema():
    return Draft202012Validator(json.loads((ROOT / "apparatus/data/data-event.schema.json").read_text()))


def test_start_day_and_closure_rows_are_closed_records(schema):
    """REQ-INT-02, REQ-INT-03: the first accepted start records only the visit session and
    the station calendar day; a refused resumption records only its bounded code."""
    start = event("visit_start", {"session_id": SESSION, "local_date": "2027-01-04"})
    closed = event("visit_exit", {"code": "RESUME_OUTSIDE_SESSION"})
    for row in (start, closed):
        schema.validate(row)
        validate_data_payload(row, "b" * 64)
    for change in ({"local_date": "4 Jan 2027"}, {"session_id": "DEMO"}, {"utc": "2027-01-04T09:00:00Z"}):
        bad = copy.deepcopy(start)
        bad["payload"].update(change)
        assert list(schema.iter_errors(bad))
    extra = copy.deepcopy(start)
    extra["payload"]["utc"] = "2027-01-04T09:00:00Z"
    with pytest.raises(EvidenceError):
        validate_data_payload(extra, "b" * 64)


def test_refusal_codes_fit_the_closure_record(schema):
    """REQ-INT-04: both refusal codes are valid ``visit_exit`` codes, so a refused battery
    is closed in the raw record rather than left open."""
    for code in ("RESUME_OUTSIDE_SESSION", "RESUME_OUTSIDE_WINDOW"):
        schema.validate(event("visit_exit", {"code": code}))
    assert list(schema.iter_errors(event("visit_exit", {"code": "resume_outside_session"})))
