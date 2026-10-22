"""Unity-produced soak-feed fixture through the normalizer into the analyzer.

``tests/fixtures/soak_unity_feed`` holds one synthetic driver run against the
in-process fake command server (``soak_unity_feed_fixture.py``) and the bytes the
Unity PlayMode test ``DriverFixtureReplaysThroughRealHostAndDataJournal`` wrote
while consuming that exact feed: the real ``SoakCaptureHost`` native journal and
the real ``DataJournal`` segment. The replay used local test frames in a headless
editor (no render callbacks, so the monitor latched a protected freeze) and lasts
under a second. It is not a station run and not soak evidence.
"""
import hashlib
import json
from pathlib import Path

import pytest

from isaac.soak.analyze import analyze_events
from isaac.soak.driver import FEED_FIELDS, load_schedule, read_chain
from isaac.soak.native import NativeReader, plan_bytes
from isaac.soak.normalize import normalize
from tools.mock_visit.records import verify_chain

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures' / 'soak_unity_feed'


def read(name):
    return (FIXTURE / name).read_bytes()


def inputs(**changes):
    plan = read('station-plan.json')
    values = dict(plan_raw=plan, plan_sha256=hashlib.sha256(plan).hexdigest(), native_raw=read('soak-native.jsonl'),
                  schedule_raw=read('schedule.json'), command_logs=[read('command-0.jsonl')],
                  driver_raw=read('driver-journal.jsonl'), feed_raw=read('unity-inputs.jsonl'),
                  data_files=[('data-events.jsonl', read('data-events.jsonl'))])
    values.update(changes)
    return values


def native_rows():
    raw = read('station-plan.json')
    reader = NativeReader(plan_bytes(raw, hashlib.sha256(raw).hexdigest()), hashlib.sha256(raw).hexdigest())
    for line in read('soak-native.jsonl').splitlines(keepends=True):
        reader.feed(line)
    reader.finish()
    return reader.rows


def test_fixture_bindings_are_the_pinned_driver_run():
    schedule = read('schedule.json')
    pin = hashlib.sha256(schedule).hexdigest()
    assert json.loads(read('station-plan.json'))['schedule_sha256'] == pin
    load_schedule(schedule, pin)
    feed = read_chain(read('unity-inputs.jsonl'), frozenset(FEED_FIELDS))
    assert {row['schedule_sha256'] for row in feed} == {pin}
    rows = verify_chain([('data-events.jsonl', read('data-events.jsonl'))], 'data')
    assert {r['payload']['schedule_sha256'] for r in rows} == {pin}
    assert {r['payload']['audible_status'] for r in rows} == {'NoCue'}
    assert not any(r['payload']['exposure_consumed'] for r in rows)


def test_unity_journal_uses_only_the_contracted_native_kinds():
    kinds = {r['kind'] for r in native_rows()}
    assert kinds <= {'session_start', 'session_end', 'heartbeat', 'context', 'stale_gap_started', 'stale_gap',
                     'frame_freeze_started', 'frame_freeze', 'reset_receipt', 'lock_probe_receipt',
                     'fault_injection', 'durable_record', 'operator_resume'}
    assert 'cue_observation' not in kinds   # No cue is played by the feed consumer.


def test_unity_fixture_normalizes_and_the_analyzer_still_refuses():
    events, summary = normalize(**inputs())
    counts = summary['counts']
    assert counts['resets'] == 14 and counts['lock_probes'] == 4 and counts['trials'] == 11 and counts['faults'] == 1
    assert counts['unplaced_rejected_lock_probes'] == 0 and counts['unplaced_successful_resets'] == 0
    assert counts['nonfault_pauses'] == 0
    assert summary['recommendation'] == 'NO_GO' and summary['analysis_required'] and summary['g2_signed'] is False
    kinds = [e['kind'] for e in events]
    for kind in ('reset', 'trial_begin', 'lock_probe', 'record_commit', 'fault', 'pause', 'resume'):
        assert kind in kinds
    assert 'exposure' not in kinds and 'cue_playback' not in kinds
    assert all(e['rejected'] for e in events if e['kind'] == 'lock_probe')
    fault = next(e for e in events if e['kind'] == 'fault')
    resume = next(e for e in events if e['kind'] == 'resume')
    commits = [e['sha256'] for e in events if e['kind'] == 'record_commit' and e['seq'] < fault['seq']]
    assert fault['fault_type'] == 'wifi_drop' and fault['last_committed_sha256'] == commits[-1]
    assert resume['last_committed_sha256'] == commits[-1] and resume['operator_initiated'] is True
    recovery = next(e for e in events if e['kind'] == 'reset' and e.get('fault_id') == fault['fault_id'])
    assert recovery['reset_ok'] is True and resume['reset_id'] == recovery['reset_id']
    assert kinds.index('pause') < kinds.index('resume') and kinds.index('fault') < kinds.index('pause')
    # An incomplete, sub-second headless replay: the analyzer refuses (NO_GO).
    with pytest.raises(ValueError, match='Session incomplete'):
        analyze_events(events)


def tamper_native(change):
    """Re-sign one changed Unity row so only the join, not the chain, can refuse it."""
    from test_soak_native import Fixture
    raw = read('station-plan.json')
    fixture = Fixture(json.loads(raw))
    fixture.raw, fixture.pin = raw, hashlib.sha256(raw).hexdigest()
    rows = native_rows()
    change(rows)
    for row in rows:
        fixture.row(row['kind'], row['t_s'], row['payload'])
    native = b''.join(fixture.lines)
    return dict(native_raw=native)


def first(kind):
    def change(rows):
        row = next(r for r in rows if r['kind'] == kind)
        return row['payload']
    return change


@pytest.mark.parametrize('mutate,match', [
    (lambda rows: first('reset_receipt')(rows).update(reply_sha256='0' * 64), 'Receipt differs'),
    (lambda rows: first('durable_record')(rows).update(data_sha256='0' * 64), 'durable data record'),
    (lambda rows: first('fault_injection')(rows).update(fault_id='0' * 32), 'Fault observation'),
    (lambda rows: rows.remove(next(r for r in rows if r['kind'] == 'fault_injection')),
     'outside its Unity-observed fault'),
])
def test_normalizer_refuses_a_changed_unity_observation(mutate, match):
    with pytest.raises(ValueError, match=match):
        normalize(**inputs(**tamper_native(mutate)))


def test_normalizer_refuses_changed_unity_data_bytes():
    data = read('data-events.jsonl').replace(b'"response_code":"commit"', b'"response_code":"timeout"', 1)
    assert data != read('data-events.jsonl')
    with pytest.raises(ValueError):
        normalize(**inputs(data_files=[('data-events.jsonl', data)]))
