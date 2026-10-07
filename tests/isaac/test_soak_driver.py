"""Synthetic soak-driver contract tests with an in-process fake command server.

The fake server wraps the real CommandDispatcher and DurableCommandLog with a
fake reset adapter; no Isaac, network, wall-clock wait or hardware is used. A
synthetic Unity journal is derived from the driver output only to exercise the
normalizer and analyzer contracts. None of this is soak evidence.
"""
import copy
import hashlib
import json
import uuid

import pytest

from test_reset import manager
from test_soak_native import Fixture, native_plan
from isaac.commands import CommandDispatcher
from isaac.commands import health_probe
from isaac.commands.event_log import DurableCommandLog
from isaac.commands.protocol import LEGAL_PAIRS, decode
from isaac.reset.snapshot import canonical_bytes
from isaac.soak.analyze import analyze_events
from isaac.soak.driver import (FEED_FIELDS, JOURNAL_FIELDS, Halt, OperatorFileFaultHook, SoakDriver,
                               build_schedule, check_steps, load_schedule, read_chain)
from isaac.soak.normalize import normalize

FAULTS = ['isaac_crash', 'wifi_drop', 'uplink_disconnect']


class Clock:
    def __init__(self):
        self.ns = 10**12

    def __call__(self):
        return self.ns

    def sleep(self, seconds):
        assert 0 < seconds <= 1.0
        self.ns += int(seconds * 1e9)

    def advance(self, seconds):
        self.ns += int(seconds * 1e9)


class FakeServer:
    """Real dispatcher semantics and durable log format; synchronous transport."""

    def __init__(self, directory, clock, mutate=None, backend=None):
        self.backend = backend   # Consistently changes both the durable log and the reply.
        self.directory, self.clock, self.mutate = directory / ('server-' + uuid.uuid4().hex[:8]), clock, mutate
        self.directory.mkdir(parents=True)
        self.logs, self.received = [], []
        self.restart()

    def restart(self):
        adapter, _, _, reset = manager()
        reset.reset()

        def demo(action, target):
            yield 'safe_point'
            return {'execution_ok': True}

        def hold(value):
            adapter.state['robot'] = copy.deepcopy(value)
        path = self.directory / f'command-{len(self.logs)}.jsonl'
        self.log = DurableCommandLog(path, session_id=uuid.uuid4().hex, apparatus_version='unit-fixture',
                                     protocol_version='unit-fixture')
        self.logs.append(path)
        self.dispatcher = CommandDispatcher(reset, self.sink, station_id='station-01', allowed_client='127.0.0.1',
                                            demo_factory=demo, hold_robot=hold)

    def sink(self, event):
        if self.backend:
            self.backend(event['reply'], event['command'])
        self.log(event)

    @property
    def session(self):
        return self.dispatcher.control_session_id

    def exchange(self, text, timeout):
        self.clock.advance(.003)
        value = decode(text)
        self.received.append(value['request_id'])
        if value['kind'] == health_probe.REQUEST_KIND:
            reply = health_probe.reply(value, self.session, self.dispatcher.health)
        else:
            future = self.dispatcher.submit(text, '127.0.0.1')
            for _ in range(5):
                if future.done():
                    break
                self.dispatcher.advance()
            reply = future.result(timeout=.2)
            if self.backend:
                self.backend(reply, value.get('command'))
        raw = json.dumps(reply)
        return self.mutate(raw, value) if self.mutate else raw

    def reconnect(self):
        pass

    def command_logs(self):
        self.log.close()
        return [p.read_bytes() for p in self.logs]


def schedule_bytes(**kwargs):
    raw = canonical_bytes(build_schedule(**kwargs))
    return raw, hashlib.sha256(raw).hexdigest()


SHORT = dict(station_id='station-01', seed=58, seconds=1200, block_seconds=300, trial_interval_s=20)
LONG = dict(station_id='station-01', seed=7, seconds=28800, block_seconds=1800, trial_interval_s=600)


def drive(tmp_path, params=SHORT, *, mutate=None, name='driver', server=None, clock=None, **kwargs):
    clock = clock or Clock()
    server = server or FakeServer(tmp_path, clock, mutate)
    raw, pin = schedule_bytes(**params)
    output = tmp_path / name
    result = SoakDriver(raw, pin, output, server, control_session_id=server.session, clock=clock,
                        sleep=clock.sleep, **kwargs).run()
    return result, server, output, raw, clock


def journal(output):
    return read_chain((output / 'driver-journal.jsonl').read_bytes(), frozenset(JOURNAL_FIELDS))


def feed(output):
    return read_chain((output / 'unity-inputs.jsonl').read_bytes(), frozenset(FEED_FIELDS))


# --------------------------------------------------------------------------
# Schedule determinism and gating
# --------------------------------------------------------------------------

def test_schedule_is_deterministic_and_seed_bound():
    first, pin = schedule_bytes(**SHORT)
    assert first == schedule_bytes(**SHORT)[0]
    assert first != schedule_bytes(**dict(SHORT, seed=59))[0]
    assert first != schedule_bytes(**dict(SHORT, station_id='station-02'))[0]
    document = load_schedule(first, pin)
    ops = [s['op'] for s in document['steps']]
    assert {'demo', 'lock_probe', 'dummy_response', 'reset', 'trial'} <= set(ops)
    assert 'fault_slot' not in ops


def test_schedule_refuses_noncanonical_or_hand_edited_bytes():
    raw, pin = schedule_bytes(**SHORT)
    pretty = json.dumps(json.loads(raw), indent=1).encode()
    with pytest.raises(ValueError, match='deterministic'):
        load_schedule(pretty, hashlib.sha256(pretty).hexdigest())
    document = json.loads(raw)
    demo = next(s for s in document['steps'] if s['op'] == 'demo')
    demo['action'], demo['target'] = next(p for p in sorted(LEGAL_PAIRS) if p != (demo['action'], demo['target']))
    edited = canonical_bytes(document)
    with pytest.raises(ValueError, match='deterministic'):
        load_schedule(edited, hashlib.sha256(edited).hexdigest())
    with pytest.raises(ValueError, match='pin'):
        load_schedule(raw, '0' * 64)


def mutate_step(document, op, **changes):
    step = next(s for s in document['steps'] if s['op'] == op)
    step.update(changes)


@pytest.mark.parametrize('change,match', [
    (lambda d: mutate_step(d, 'demo', block='protected'), 'outside its block'),
    (lambda d: [s.update(op='demo') for s in d['steps'] if s['op'] == 'lock_probe'][:1], 'fields|Demo'),
    (lambda d: d['steps'].__setitem__(slice(None), [s for s in d['steps'] if s['op'] != 'lock_probe']) or
     [s.update(index=i) for i, s in enumerate(d['steps'])], 'lock probe'),
    (lambda d: d['steps'].__setitem__(slice(None), [s for s in d['steps'] if s['op'] != 'reset']) or
     [s.update(index=i) for i, s in enumerate(d['steps'])], 'fresh reset'),
    (lambda d: d.update(min_command_interval_s=30), 'rate'),
])
def test_semantic_gate_rejects_unsafe_schedules(change, match):
    document = build_schedule(**SHORT)
    change(document)
    with pytest.raises(ValueError, match=match):
        check_steps(document)


def test_demos_only_in_teaching_and_probes_only_in_test():
    document = build_schedule(**dict(LONG, fault_types=FAULTS))
    mode = 'test'
    for step in document['steps']:
        if step['op'] == 'set_mode':
            mode = step['mode']
        if step['op'] == 'demo':
            assert mode == 'teaching' and step['block'] == 'teaching'
        if step['op'] in ('lock_probe', 'dummy_response', 'fault_slot'):
            assert mode == 'test' and step['block'] == 'protected'
    assert sorted(s['fault_type'] for s in document['steps'] if s['op'] == 'fault_slot') == sorted(FAULTS)


def test_runtime_mode_gate_refuses_before_sending(tmp_path):
    clock = Clock(); server = FakeServer(tmp_path, clock)
    raw, pin = schedule_bytes(**SHORT)
    driver = SoakDriver(raw, pin, tmp_path / 'gate', server, control_session_id=server.session, clock=clock,
                        sleep=clock.sleep)
    demo = next(s for s in driver.schedule['steps'] if s['op'] == 'demo')
    probe = next(s for s in driver.schedule['steps'] if s['op'] == 'lock_probe')
    driver.mode = 'test'
    with pytest.raises(Halt):
        driver.gate(demo)
    driver.mode = 'teaching'
    with pytest.raises(Halt):
        driver.gate(probe)
    driver.journal.close(); driver.feed.close()
    assert not server.received


# --------------------------------------------------------------------------
# Driver behavior
# --------------------------------------------------------------------------

def test_driver_completes_with_bounded_rate_and_durable_records(tmp_path):
    result, server, output, raw, _ = drive(tmp_path)
    assert result['completed'] and result['recommendation'] == 'NO_GO' and result['g2_signed'] is False
    rows = journal(output)
    replies = [r for r in rows if r['kind'] == 'command_reply']
    intents = [r for r in rows if r['kind'] == 'command_intent']
    assert len(replies) == len(intents) and all(r['payload']['outcome'] == 'expected' for r in replies)
    assert len(set(server.received)) == len(server.received)
    sent = [int(r['payload']['sent_ns']) for r in replies]
    assert all(b - a >= 2e9 for a, b in zip(sent, sent[1:]))
    probes = [r for r in replies if r['payload']['op'] == 'lock_probe']
    assert probes and all(decode(r['payload']['reply_utf8'])['reason'] == 'PROTECTED_TARGET_COMMAND' for r in probes)
    logged = [json.loads(line) for data in server.command_logs() for line in data.splitlines()]
    assert len(logged) == len(replies)
    kinds = [r['kind'] for r in feed(output)]
    assert kinds.count('command_ack') == len(replies) and 'dummy_response' in kinds and 'trial' in kinds


def test_late_driver_never_bursts(tmp_path):
    clock = Clock()

    def late(raw, value):
        if value.get('command') == 'set_mode':
            clock.advance(100)
        return raw
    result, _, output, _, _ = drive(tmp_path, mutate=late, clock=clock)
    assert result['completed']
    sent = [int(r['payload']['sent_ns']) for r in journal(output) if r['kind'] == 'command_reply']
    assert all(b - a >= 2e9 for a, b in zip(sent, sent[1:]))


def corrupt(change):
    def mutate(raw, value):
        if value.get('command') == 'reset':
            return change(raw, value)
        return raw
    return mutate


def edit(field, value):
    def change(raw, _):
        reply = json.loads(raw); reply[field] = value
        return json.dumps(reply)
    return change


@pytest.mark.parametrize('change,code', [
    (lambda raw, _: raw[:-1], 'REPLY_MALFORMED'),
    (lambda raw, _: raw.encode(), 'REPLY_NOT_TEXT'),
    (edit('request_id', 'f' * 32), 'REPLY_REQUEST_BINDING'),
    (edit('reason', 'ALL_GOOD'), 'REPLY_UNKNOWN_REASON'),
    (edit('duplicate', True), 'REPLY_UNEXPECTED_DUPLICATE'),
    (edit('extra', 1), 'REPLY_SHAPE'),
    (edit('reset_ok', False), 'REPLY_INCONSISTENT'),
    (lambda raw, _: json.dumps(dict(json.loads(raw), health=dict(json.loads(raw)['health'], control_session_id='e' * 32))),
     'REPLY_HEALTH_SESSION'),
])
def test_unknown_or_malformed_replies_halt_without_retry(tmp_path, change, code):
    result, server, output, _, _ = drive(tmp_path, mutate=corrupt(change))
    assert not result['completed'] and result['halted'] == 'REPLY_REFUSED'
    rows = journal(output)
    halt = next(r for r in rows if r['kind'] == 'halt')
    assert halt['payload']['error'] == code
    resets = [r for r in rows if r['kind'] == 'command_intent' and r['payload']['op'] == 'reset']
    assert len(resets) == 1 and rows[-1]['kind'] == 'driver_end'
    assert server.received.count(resets[0]['payload']['request_id']) == 1
    assert not any(r['kind'] == 'command_ack' and r['payload']['op'] == 'reset' for r in feed(output))


def test_well_formed_failure_is_forwarded_then_halts(tmp_path):
    def accepted_probe(raw, value):
        if value.get('command') == 'demo' and json.loads(raw)['mode'] == 'test':
            reply = json.loads(raw)
            reply.update(accepted=True, reason='DEMO_COMPLETE')
            return json.dumps(reply)
        return raw
    result, _, output, _, _ = drive(tmp_path, mutate=accepted_probe)
    assert result['halted'] == 'UNEXPECTED_OUTCOME'
    acks = [r for r in feed(output) if r['kind'] == 'command_ack' and r['payload']['op'] == 'lock_probe']
    assert len(acks) == 1 and json.loads(acks[0]['payload']['reply_utf8'])['accepted'] is True


def test_transport_failure_is_recorded_once_and_never_retried(tmp_path):
    clock = Clock(); server = FakeServer(tmp_path, clock)
    calls = []

    class Flaky:
        session = server.session

        def exchange(self, text, timeout):
            calls.append(text)
            if decode(text).get('command') == 'reset':
                server.exchange(text, timeout)
                raise TimeoutError('no reply')
            return server.exchange(text, timeout)

        def reconnect(self):
            pass
    result, _, output, _, _ = drive(tmp_path, server=Flaky(), clock=clock)
    assert result['halted'] == 'TRANSPORT_ERROR'
    assert sum(decode(c).get('command') == 'reset' for c in calls) == 1


def test_missing_demo_content_requires_explicit_allowance(tmp_path):
    def missing(raw, value):
        reply = json.loads(raw)
        if value.get('command') == 'demo' and reply['mode'] == 'teaching':
            reply.update(accepted=False, reason='DEMO_NOT_IMPLEMENTED')
        return json.dumps(reply)
    result, *_ = drive(tmp_path, mutate=missing)
    assert result['halted'] == 'UNEXPECTED_OUTCOME'
    result, _, output, _, _ = drive(tmp_path, mutate=missing, name='allowed', allow_missing_demo_content=True)
    assert result['completed']
    assert any(r['payload'].get('outcome') == 'demo_content_missing' for r in journal(output))


# --------------------------------------------------------------------------
# Fault hooks are explicit
# --------------------------------------------------------------------------

def test_fault_slots_are_inert_without_explicit_authorization(tmp_path):
    clock = Clock(); server = FakeServer(tmp_path, clock)
    raw, pin = schedule_bytes(**dict(LONG, fault_types=FAULTS))
    called = []
    with pytest.raises(ValueError, match='authorization'):
        SoakDriver(raw, pin, tmp_path / 'a', server, control_session_id=server.session, fault_hook=called.append)
    with pytest.raises(ValueError, match='hook'):
        SoakDriver(raw, pin, tmp_path / 'b', server, control_session_id=server.session, authorize_fault_injection=True)
    result, _, output, _, _ = drive(tmp_path, dict(LONG, fault_types=FAULTS), server=server, clock=clock)
    assert result['completed'] and not called
    assert sum(r['kind'] == 'fault_slot_not_injected' for r in journal(output)) == 3
    assert not any(r['kind'] == 'fault_marker' for r in feed(output))


def test_operator_file_hook_waits_for_explicit_confirmation(tmp_path):
    clock = Clock()
    hook = OperatorFileFaultHook(tmp_path, timeout_s=5, clock=clock, sleep=clock.sleep)
    request = dict(fault_id='a' * 32, fault_type='wifi_drop', station_id='station-01', step_index=4)
    with pytest.raises(ValueError, match='timeout'):
        hook(request)
    assert json.loads((tmp_path / f"fault-request-{'a' * 32}.json").read_text())['fault_type'] == 'wifi_drop'

    def confirm(seconds):
        clock.advance(seconds)
        (tmp_path / f"fault-done-{'b' * 32}.json").write_bytes(canonical_bytes(
            dict(version=1, fault_id='b' * 32, fault_type='wifi_drop', performed=True)))
    hook = OperatorFileFaultHook(tmp_path, timeout_s=5, clock=clock, sleep=confirm)
    assert hook(dict(request, fault_id='b' * 32))['method'] == 'operator_file'


def test_recovery_never_resumes_without_operator_file(tmp_path):
    def inject(request):
        return dict(method='unit_fixture')
    result, _, output, _, _ = drive(tmp_path, dict(LONG, fault_types=['wifi_drop']), fault_hook=inject,
                                    authorize_fault_injection=True, recovery_timeout_s=5)
    assert result['halted'] == 'RECOVERY_TIMEOUT'
    assert not any(r['kind'] == 'fault_recovered' for r in journal(output))


# --------------------------------------------------------------------------
# Interruption and resume
# --------------------------------------------------------------------------

class HardKill(BaseException):
    pass


def test_interrupted_driver_resumes_at_block_boundary_without_replay(tmp_path):
    clock = Clock(); server = FakeServer(tmp_path, clock)
    raw, pin = schedule_bytes(**SHORT)
    count = []

    class Dying:
        def exchange(self, text, timeout):
            reply = server.exchange(text, timeout)
            if decode(text).get('command') == 'demo':
                count.append(1)
                if len(count) == 3:
                    raise HardKill()
            return reply

        def reconnect(self):
            pass
    output = tmp_path / 'driver'
    with pytest.raises(HardKill):
        SoakDriver(raw, pin, output, Dying(), control_session_id=server.session, clock=clock, sleep=clock.sleep).run()
    killed = journal(output)
    assert killed[-1]['kind'] == 'command_intent'
    lost = killed[-1]['payload']
    with pytest.raises(ValueError, match='Fresh'):
        SoakDriver(raw, pin, output, server, control_session_id=server.session, clock=clock, sleep=clock.sleep)
    result = SoakDriver(raw, pin, output, server, control_session_id=server.session, clock=clock,
                        sleep=clock.sleep, resume=True).run()
    assert result['completed']
    rows = journal(output)
    assert any(r['kind'] == 'interrupted_exchange' and r['payload']['request_id'] == lost['request_id'] for r in rows)
    skipped = next(r['payload'] for r in rows if r['kind'] == 'skipped')
    assert skipped['reason'] == 'RESUME_BOUNDARY' and skipped['from_index'] == lost['step_index'] + 1
    schedule = load_schedule(raw, pin)
    assert schedule['steps'][skipped['to_index'] + 1]['op'] == 'block_begin'
    assert len(set(server.received)) == len(server.received)
    again = SoakDriver(raw, pin, output, server, control_session_id=server.session, clock=clock,
                       sleep=clock.sleep, resume=True)
    try:
        with pytest.raises(ValueError, match='Completed'):
            again.resume_point()
    finally:
        again.journal.close(); again.feed.close()


def test_torn_journal_tail_requires_operator_review(tmp_path):
    result, server, output, raw, clock = drive(tmp_path)
    path = output / 'driver-journal.jsonl'
    path.write_bytes(path.read_bytes()[:-5])
    with pytest.raises(ValueError, match='Torn'):
        SoakDriver(raw, hashlib.sha256(raw).hexdigest(), output, server, control_session_id=server.session,
                   resume=True)


# --------------------------------------------------------------------------
# Normalization round trip into the existing analyzer
# --------------------------------------------------------------------------

class DataJournal:
    def __init__(self, schedule_sha256):
        self.rows, self.previous, self.schedule_sha256 = [], '0' * 64, schedule_sha256

    def session(self, t, event, trial_id, **changes):
        payload = dict(event=event, clock_epoch='d' * 32, schedule_sha256=self.schedule_sha256, trial_id=trial_id,
                       retry_of=None, block_index=0, item_index=0, host_mono_ms=t * 1000,
                       scheduled_onset_mono_ms=None, state=None, audible_status='NoCue', exposure_consumed=False,
                       reset_ok=True, focus_ok=True, technical_fault_code=None, response_code=None,
                       evidence_sha256=None, opportunity_id=trial_id, audio_request_ids=[])
        payload.update(changes)
        row = dict(schema_version='data-events-provisional-1', sequence=len(self.rows), event_id=uuid.uuid4().hex,
                   clock_epoch='d' * 32, host_mono_ms=t * 1000,
                   identity=dict(session_id='a' * 32, coded_id='SOAK-SYNTHETIC', visit_id='soak-synthetic',
                                 station_id='station-01', protocol_version='soak-synthetic', build_sha256='b' * 64),
                   event_type='session', opportunity_id=trial_id, attempt_id=trial_id, audio_request_id=None,
                   previous_sha256=self.previous, payload=payload)
        unhashed = json.dumps(row, separators=(',', ':'), ensure_ascii=False) + '\n'
        row['sha256'] = self.previous = hashlib.sha256(unhashed.encode()).hexdigest()
        self.rows.append(row)
        return row

    def raw(self):
        return b''.join((json.dumps(r, separators=(',', ':'), ensure_ascii=False) + '\n').encode() for r in self.rows)


def unity_fixture(output, schedule_raw, seconds, mutate=None):
    """Derive synthetic Unity native rows that a correct joined host would log."""
    pin = hashlib.sha256(schedule_raw).hexdigest()
    rows = journal(output)
    origin = int(rows[0]['monotonic_ns'])
    when = {r['payload']['feed_seq']: 10 + (int(r['monotonic_ns']) - origin) / 1e9 + .05
            for r in rows if r['kind'] == 'unity_input'}
    data = DataJournal(pin)
    obs, committed, block = [], None, None
    for row in feed(output):
        t, p, kind = when[row['seq']], row['payload'], row['kind']
        if kind == 'block_begin':
            block = dict(block=p['block'], block_id=p['block_id'], engine_state='running', trial_id=None)
            obs.append((t, 'context', block))
        elif kind == 'command_ack':
            reply = json.loads(p['reply_utf8'])
            receipt = dict(input_seq=row['seq'], request_id=p['request_id'], reply_sha256=p['reply_sha256'])
            if p['op'] == 'reset' or (p['op'] == 'set_mode' and reply['reset_ok'] is not None):
                obs.append((t, 'reset_receipt', receipt))
                if p['fault_id']:
                    record = data.session(t + 1, 'operator_resume', None)
                    obs.append((t + 1, 'operator_resume', dict(
                        fault_id=p['fault_id'], data_event_id=record['event_id'], data_sha256=record['sha256'],
                        reset_request_id=p['request_id'], last_committed_data_sha256=committed)))
                    obs.append((t + 1.1, 'context', block))
            elif p['op'] == 'lock_probe':
                obs.append((t, 'lock_probe_receipt', receipt))
        elif kind == 'trial':
            record = data.session(t, 'state_before', p['trial_id'])
            obs.append((t, 'durable_record', dict(data_event_id=record['event_id'], data_sha256=record['sha256'],
                                                  reset_request_id=p['reset_request_id'])))
        elif kind == 'dummy_response':
            cue = data.session(t, 'onset_evidence', p['trial_id'], audible_status='Uncertain', exposure_consumed=True)
            obs.append((t, 'cue_observation', dict(data_event_id=cue['event_id'], data_sha256=cue['sha256'])))
            record = data.session(t + .01, 'response', p['trial_id'], response_code=p['response_code'])
            committed = record['sha256']
            obs.append((t + .01, 'durable_record', dict(data_event_id=record['event_id'], data_sha256=committed,
                                                        reset_request_id=None)))
        elif kind == 'fault_marker':
            obs.append((t + .1, 'fault_injection', dict(input_seq=row['seq'], fault_id=p['fault_id'],
                                                        last_committed_data_sha256=committed)))
            obs.append((t + .15, 'context', dict(block, block='paused', engine_state='fault_paused')))
            record = data.session(t + .2, 'session_paused', None)
            obs.append((t + .2, 'durable_record', dict(data_event_id=record['event_id'], data_sha256=record['sha256'],
                                                       reset_request_id=None)))
    if mutate:
        mutate(obs, data)
    plan = dict(native_plan(seconds), schedule_sha256=pin)
    fixture = Fixture(plan)
    fixture.start()
    contexts = sorted((t, p) for t, k, p in obs if k == 'context')
    heartbeats, current, position = [], dict(block='teaching', block_id='idle'), 0
    for second in range(seconds + 1):
        while position < len(contexts) and contexts[position][0] <= 10 + second:
            current = contexts[position][1]; position += 1
        heartbeats.append((10 + second, 'heartbeat', dict(block=current['block'], block_id=current['block_id'],
                                                          state_age_ms=0., frame_age_ms=0., mirrored_frames=second + 1)))
    for t, kind, payload in sorted(obs + heartbeats, key=lambda x: x[0]):
        fixture.row(kind, t, payload)
    fixture.end(10. + seconds)
    return fixture, data


def run_normalize(server, output, schedule_raw, fixture, data, **changes):
    inputs = dict(plan_raw=fixture.raw, plan_sha256=fixture.pin, native_raw=b''.join(fixture.lines),
                  schedule_raw=schedule_raw, command_logs=server.command_logs(),
                  driver_raw=(output / 'driver-journal.jsonl').read_bytes(),
                  feed_raw=(output / 'unity-inputs.jsonl').read_bytes(), data_files=[('data.jsonl', data.raw())])
    inputs.update(changes)
    return normalize(**inputs)


@pytest.fixture(scope='module')
def fault_run(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp('fault-run')
    clock = Clock(); server = FakeServer(tmp_path, clock)
    calls = []

    def inject(request):
        calls.append(request)
        if request['fault_type'] == 'isaac_crash':
            server.restart()
        clock.advance(30)
        (tmp_path / 'driver' / 'operator' / f"recovery-{request['fault_id']}.json").write_bytes(canonical_bytes(
            dict(version=1, fault_id=request['fault_id'], control_session_id=server.session, operator_initiated=True)))
        return dict(method='unit_fixture')
    result, _, output, raw, _ = drive(tmp_path, dict(LONG, fault_types=FAULTS), server=server, clock=clock,
                                      fault_hook=inject, authorize_fault_injection=True)
    return result, server, output, raw, calls


def test_fault_run_recovers_with_operator_gate_and_new_session(fault_run):
    result, server, output, _, calls = fault_run
    assert result['completed'] and [c['fault_type'] for c in calls] == FAULTS
    rows = journal(output)
    assert sum(r['kind'] == 'fault_recovered' for r in rows) == 3
    assert len(server.logs) == 2   # The crash fixture restarted the fake service.
    recovery = [r['payload'] for r in rows if r['kind'] == 'command_reply' and r['payload']['recovery']]
    assert len(recovery) == 3 and all(decode(r['reply_utf8'])['reset_ok'] is True for r in recovery)


def test_normalized_round_trip_is_judged_by_existing_analyzer(fault_run):
    _, server, output, raw, _ = fault_run
    fixture, data = unity_fixture(output, raw, 28900)
    events, summary = run_normalize(server, output, raw, fixture, data)
    report = analyze_events(events)
    assert report['failures'] == [] and report['faults'] == 3 and report['lock_probes'] > 0
    assert summary['recommendation'] == 'NO_GO' and summary['analysis_required'] and summary['g2_signed'] is False
    assert summary['counts']['unplaced_rejected_lock_probes'] == 0
    assert {e['kind'] for e in events} >= {'reset', 'trial_begin', 'lock_probe', 'fault', 'pause', 'resume',
                                          'record_commit', 'exposure', 'cue_playback'}


def test_uninjected_faults_cannot_become_a_pass(tmp_path):
    result, server, output, raw, _ = drive(tmp_path, dict(LONG, fault_types=FAULTS))
    fixture, data = unity_fixture(output, raw, 28900)
    events, _ = run_normalize(server, output, raw, fixture, data)
    assert 'missing_fault_type' in analyze_events(events)['failures']


@pytest.fixture(scope='module')
def short_run(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp('short-run')
    result, server, output, raw, _ = drive(tmp_path)
    assert result['completed']
    return server, output, raw


def test_short_round_trip_normalizes_but_analyzer_still_refuses_duration(short_run):
    server, output, raw = short_run
    fixture, data = unity_fixture(output, raw, 1300)
    events, summary = run_normalize(server, output, raw, fixture, data)
    assert summary['counts']['lock_probes'] > 0 and summary['counts']['trials'] > 0
    with pytest.raises(ValueError, match='eight'):
        analyze_events(events)


def first(obs, kind):
    return next(i for i, x in enumerate(obs) if x[1] == kind)


def replace(kind, **changes):
    def mutate(obs, data):
        index = first(obs, kind)
        t, k, p = obs[index]
        obs[index] = (t, k, dict(p, **changes))
    return mutate


@pytest.mark.parametrize('mutate,match', [
    (replace('reset_receipt', reply_sha256='0' * 64), 'Receipt differs'),
    (replace('lock_probe_receipt', request_id='0' * 32), 'Receipt differs'),
    (replace('durable_record', data_sha256='0' * 64), 'durable data record'),
    (replace('durable_record', reset_request_id='0' * 32), 'reset acknowledgement'),
    (lambda obs, data: obs.append(next(x for x in obs if x[1] == 'reset_receipt')), 'Duplicate Unity receipt'),
    (lambda obs, data: obs.append((obs[first(obs, 'lock_probe_receipt')][0] - .001, 'context',
                                   dict(block='teaching', block_id='x', engine_state='running', trial_id=None))) or
     obs.sort(key=lambda x: x[0]), 'outside protected'),
    (lambda obs, data: obs.append((500., 'unknown_observation', {})), 'Unknown'),
])
def test_normalizer_refuses_unmatched_or_unknown_records(short_run, mutate, match):
    server, output, raw = short_run
    fixture, data = unity_fixture(output, raw, 1300, mutate=mutate)
    with pytest.raises(ValueError, match=match):
        run_normalize(server, output, raw, fixture, data)


def test_failing_lock_probe_without_receipt_is_refused_not_dropped(tmp_path):
    def not_ready(reply, command):
        if command == 'demo' and reply['mode'] == 'test':
            reply.update(reason='NOT_READY')
    clock = Clock()
    server = FakeServer(tmp_path, clock, backend=not_ready)
    result, server, output, raw, _ = drive(tmp_path, server=server, clock=clock)
    assert result['halted'] == 'UNEXPECTED_OUTCOME'
    drop = lambda obs, data: obs.pop(first(obs, 'lock_probe_receipt'))
    fixture, data = unity_fixture(output, raw, 1300, mutate=drop)
    with pytest.raises(ValueError, match='Failing lock probe'):
        run_normalize(server, output, raw, fixture, data)
    # With its receipt the same failure is placed and the analyzer sees it.
    fixture, data = unity_fixture(output, raw, 1300)
    events, _ = run_normalize(server, output, raw, fixture, data)
    assert [e['rejected'] for e in events if e['kind'] == 'lock_probe'] == [False]


def test_normalizer_refuses_tampered_driver_or_command_records(short_run):
    server, output, raw = short_run
    fixture, data = unity_fixture(output, raw, 1300)
    driver_raw = (output / 'driver-journal.jsonl').read_bytes()
    with pytest.raises(ValueError, match='chain|canonical'):
        run_normalize(server, output, raw, fixture, data, driver_raw=driver_raw.replace(b'"expected"', b'"expectex"', 1))
    logs = server.command_logs()
    event = json.loads(logs[0].splitlines()[0])
    event['payload'].update(command='demo', mode='test')
    event['payload']['reply'].update(accepted=True, request_id='c' * 32, mode='test')
    event['event_seq'] = len(logs[0].splitlines())
    tampered = logs[0] + (json.dumps(event, sort_keys=True) + '\n').encode()
    with pytest.raises(ValueError, match='Accepted protected demo'):
        run_normalize(server, output, raw, fixture, data, command_logs=[tampered])


def test_missing_unity_fault_observation_is_refused(fault_run):
    _, server, output, raw, _ = fault_run
    def drop(obs, data):
        del obs[first(obs, 'fault_injection')]
    fixture, data = unity_fixture(output, raw, 28900, mutate=drop)
    with pytest.raises(ValueError):
        run_normalize(server, output, raw, fixture, data)
