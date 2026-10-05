import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('check_data_headers', ROOT / 'tools/check_data_headers.py')
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


def test_public_header_oracles_match_runtime_exact_order():
    source = (ROOT / 'unity/Assets/ExperimentApp/Runtime/DataLogging/DataDeriver.cs').read_text()
    for field, file in [('TrialHeaders', 'trial-log.provisional.csv'), ('ExposureHeaders', 'exposure-ledger.provisional.csv')]:
        literal = re.search(field + r'=Array.AsReadOnly\(new\[\]\{(.*?)\}\);', source).group(1)
        runtime = re.findall(r'"([a-z0-9_]+)"', literal)
        assert CHECK.read_header((ROOT / 'apparatus/data' / file).read_bytes()) == runtime


def test_explicit_synthetic_review_is_required_and_order_preserved():
    a = (ROOT / 'apparatus/data/trial-log.provisional.csv').read_bytes()
    b = (ROOT / 'apparatus/data/exposure-ledger.provisional.csv').read_bytes()
    with pytest.raises(ValueError, match='REVIEW_REQUIRED'):
        CHECK.validate_headers(a, b, hashlib.sha256(a).hexdigest(), hashlib.sha256(b).hexdigest(), '')
    reversed_a = (','.join(reversed(CHECK.read_header(a))) + '\n').encode()
    result = CHECK.validate_headers(reversed_a, b, hashlib.sha256(reversed_a).hexdigest(), hashlib.sha256(b).hexdigest(), 'f' * 64)
    assert result['trial_headers'] == list(reversed(CHECK.read_header(a)))
    with pytest.raises(ValueError, match='TEMPLATE_HASH'):
        CHECK.validate_headers(a + b'x', b, hashlib.sha256(a).hexdigest(), hashlib.sha256(b).hexdigest(), 'f' * 64)


def test_synthetic_raw_events_match_closed_schema_and_reject_personal_extension():
    schema = json.loads((ROOT / 'apparatus/data/data-event.schema.json').read_text())
    validator = Draft202012Validator(schema)
    events = [json.loads(line) for line in (ROOT / 'docs/data/synthetic-visit/events.jsonl').read_text().splitlines()]
    assert len(events) > 36
    for event in events:
        validator.validate(event)
    bad = copy.deepcopy(events[0]); bad['identity']['participant_name'] = 'not allowed'
    assert list(validator.iter_errors(bad))
    bad = copy.deepcopy(events[0]); bad['payload']['payment'] = 1
    assert list(validator.iter_errors(bad))


def test_synthetic_export_hashes_bind_the_exact_published_bytes():
    folder = ROOT / 'docs/data/synthetic-visit'
    manifest = json.loads((folder / 'public-manifest.json').read_text())
    for file in manifest['files']:
        data = (folder / file['path']).read_bytes()
        assert len(data) == file['bytes']
        assert hashlib.sha256(data).hexdigest() == file['sha256']
    assert manifest['trial_rows'] == 36
    assert manifest['exposure_rows'] == 36
    assert manifest['protocol_headers_qualified'] is False


def test_assessment_stage_schema_rejects_attempt_context_and_cross_stage_ratings():
    schema = json.loads((ROOT / 'apparatus/data/data-event.schema.json').read_text())
    validator = Draft202012Validator(schema)
    event = json.loads((ROOT / 'docs/data/synthetic-visit/events.jsonl').read_text().splitlines()[0])
    event.update(event_type='assessment_stage', opportunity_id=None, attempt_id=None, audio_request_id=None)
    event['payload'] = dict(event_kind='rating', schedule_sha256='a' * 64,
                            host_mono_ms=1, clock_epoch='b' * 32, stage='forms',
                            item_id='pleasantness', value=7, outcome_code=None)
    validator.validate(event)
    for key, value in [('value', 8), ('stage', 'post_w4_optional'), ('outcome_code', 'completed')]:
        bad = copy.deepcopy(event); bad['payload'][key] = value
        assert list(validator.iter_errors(bad))
    bad = copy.deepcopy(event); bad['attempt_id'] = 'trial'
    assert list(validator.iter_errors(bad))
    event['payload'].update(event_kind='optional_help', stage='post_w4_optional', item_id='K-a1', value=None, outcome_code='completed')
    validator.validate(event)
    event['payload']['item_id'] = 'K-a1-r1'
    assert list(validator.iter_errors(event))
    event['payload'].update(event_kind='optional_execution', item_id='execute_A_ADD_ONE')
    validator.validate(event)
    event['payload']['item_id'] = 'execute_A_SCAN'
    assert list(validator.iter_errors(event))
