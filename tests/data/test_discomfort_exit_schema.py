import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]


def _session_event():
    for line in (ROOT / 'docs/data/synthetic-visit/events.jsonl').read_text().splitlines():
        event = json.loads(line)
        if event['event_type'] == 'session' and event['payload']['trial_id'] is None:
            return event
    raise AssertionError('no session event in the synthetic visit')


def test_discomfort_exit_is_a_distinct_session_event_in_the_data_schema():
    """REQ-LOG-01: a participant's discomfort stop is logged as its own session event, with no technical fault code."""
    validator = Draft202012Validator(json.loads((ROOT / 'apparatus/data/data-event.schema.json').read_text()))
    event = copy.deepcopy(_session_event())
    event['payload']['event'] = 'participant_discomfort_stop'
    validator.validate(event)
    assert event['payload']['technical_fault_code'] is None
    runtime = (ROOT / 'unity/Assets/ExperimentApp/Runtime/SessionEngine/SessionJournal.cs').read_text()
    assert '"participant_discomfort_stop"' in runtime
    bad = copy.deepcopy(event)
    bad['payload']['event'] = 'participant_withdrawal_stop'
    assert list(validator.iter_errors(bad))
