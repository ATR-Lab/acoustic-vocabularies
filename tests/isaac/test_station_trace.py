"""Continuous cross-talk trace: coverage/tolerance math and synthetic stations.

Synthetic stations run in threads; no Isaac process, socket or network is used.
"""
import hashlib
import json
import math
import threading
import time

import pytest

from isaac.publisher.protocol import PublicRegistry, StateEncoder
from isaac.stations.audit import run_cross_talk
from isaac.stations.trace import (covered_ns, deviation, evaluate_window, policy_sha256, quaternion_angle,
                                  validate_policy, window_coverage, within_tolerance)

MS = 1_000_000


def config(number=1):
    return dict(version=1, station_id='engineering-'+str(number), logical_host='fixture-host', gpu_index=number-1,
        publisher_port=20000+number*2, command_port=20001+number*2, dds_domain_id=40+number, ros_domain_id=None,
        allowed_client='127.0.0.1', allowed_uid=10000+number, scene_sha256='a'*64, reset_snapshot_sha256='b'*64,
        layout_sha256='c'*64, image_digest='sha256:'+'d'*64, source_revision='e'*40,
        publisher_hz=30, network_mode='none', ipc_mode='private', unitree_dds_enabled=False)


class Client:
    """Synthetic station: demo moves only its own robot; reset/stop restore neutral."""

    def __init__(self, value):
        self.registry = PublicRegistry(value['station_id'], value['scene_sha256'], value['reset_snapshot_sha256'],
            tuple('j'+str(i) for i in range(43)), (('fixture_object', ()),))
        self.encoder = StateEncoder(self.registry, source_kind='synthetic')
        self.q, self.step, self.transient = [0.]*43, 0, None
        self.objects = {'fixture_object': dict(position_m=[0., 0., 0.], rotation_xyzw=[0., 0., 0., 1.],
                                               visible=True, enabled=True, state={})}

    def public_state(self):
        self.step += 1
        return self.encoder.build(list(self.q), self.objects, self.step/30, self.step)

    def command(self, name, args):
        if name in ('reset', 'hold_neutral', 'stop'):
            self.q = [0.]*43
        if name == 'demo':
            self.q = [.2]+[0.]*42
        return dict(accepted=True, reset_ok=True)


def policy(**changes):
    value = dict(version=1, sample_hz=200, max_gap_s=0.25, min_coverage=1.0, settle_s=0.0,
                 joint_tolerance_rad=1e-6, position_tolerance_m=1e-6, rotation_tolerance_rad=1e-6,
                 visual_scalar_tolerance=0.0, rationale='Synthetic fixture: exact neutral expected.')
    value.update(changes)
    return value


def test_policy_is_explicit_and_bounded():
    assert validate_policy(policy()) == policy() and len(policy_sha256(policy())) == 64
    for change in (dict(rationale=' '), dict(sample_hz=0), dict(max_gap_s=0.001), dict(min_coverage=0),
                   dict(position_tolerance_m=1.0), dict(settle_s=-1), dict(joint_tolerance_rad=math.nan)):
        with pytest.raises(ValueError):
            validate_policy(policy(**change))
    with pytest.raises(ValueError):
        validate_policy({k: v for k, v in policy().items() if k != 'rationale'})


def test_coverage_counts_only_bracketed_intervals_within_max_gap():
    # Window 100..200 ms, max gap 30 ms: 120->180 is one 60 ms hole.
    times = [90*MS, 110*MS, 120*MS, 180*MS, 190*MS, 205*MS]
    covered, complete = covered_ns(times, 100*MS, 200*MS, 30*MS)
    assert covered == 40*MS and not complete
    assert window_coverage(times, 100*MS, 200*MS, 30*MS) == pytest.approx(0.4)
    dense = [t*MS for t in range(95, 210, 10)]
    assert window_coverage(dense, 100*MS, 200*MS, 30*MS) == 1.0
    # No sample after the window end: the tail is uncovered, never assumed.
    assert window_coverage([95*MS, 120*MS, 150*MS], 100*MS, 200*MS, 30*MS) == pytest.approx(0.5)
    assert window_coverage([90*MS, 110*MS], 100*MS, 100*MS, 30*MS) == 1.0
    assert window_coverage([110*MS], 100*MS, 100*MS, 30*MS) == 0.0


def frame(q0=0.0, x=0.0, rotation=(0.0, 0.0, 0.0, 1.0), visible=True, state=None):
    return dict(joint_positions=[q0]+[0.0]*42,
                objects=[dict(id='o', position_m=[x, 0.0, 0.0], rotation_xyzw=list(rotation), visible=visible,
                              enabled=True, state=state or {'lid_open_fraction': 0.0, 'tag_attached': False})])


def test_deviation_measures_joint_position_rotation_scalar_and_discrete_changes():
    base = frame()
    half = math.sin(0.05)
    dev = deviation(base, frame(q0=0.01, x=0.002, rotation=(0.0, 0.0, half, math.cos(0.05)),
                                state={'lid_open_fraction': 0.03, 'tag_attached': True}, visible=False))
    assert dev['joint_rad'] == pytest.approx(0.01) and dev['position_m'] == pytest.approx(0.002)
    assert dev['rotation_rad'] == pytest.approx(0.1) and dev['visual_scalar'] == pytest.approx(0.03)
    assert dev['discrete_mismatches'] == 2
    assert quaternion_angle([0, 0, 0, 1], [0, 0, 0, -1]) == pytest.approx(0.0)  # Same rotation.
    loose = policy(joint_tolerance_rad=0.02, position_tolerance_m=0.003, rotation_tolerance_rad=0.11,
                   visual_scalar_tolerance=0.05)
    assert not within_tolerance(dev, loose)  # Discrete changes are never tolerated.
    assert within_tolerance(dict(dev, discrete_mismatches=0), loose)
    assert not within_tolerance(dict(dev, discrete_mismatches=0), policy())


def test_window_reports_exceedance_and_excludes_target_station():
    calm, moved = deviation(frame(), frame()), deviation(frame(), frame(q0=0.2))
    samples = {'a': [(t*MS, moved) for t in range(0, 300, 5)],
               'b': [(t*MS, moved if t == 150 else calm) for t in range(0, 300, 5)]}
    rows = evaluate_window(samples, target='a', start_ns=100*MS, end_ns=200*MS, policy=policy())
    assert [r['station_id'] for r in rows] == ['b']
    assert rows[0]['exceedances'] == 1 and rows[0]['coverage'] == 1.0 and rows[0]['maxima']['joint_rad'] == pytest.approx(0.2)


class Sampler:
    """Independent synthetic public connection to a station's state."""

    def __init__(self, client, stall_on=None, stall_s=0.0):
        self.client, self.registry = client, client.registry
        self.encoder = StateEncoder(client.registry, source_kind=client.encoder.source_kind)
        self.calls, self.step, self.stall_on, self.stall_s = 0, 0, stall_on, stall_s

    def public_state(self):
        self.calls += 1
        if self.calls == self.stall_on:
            time.sleep(self.stall_s)
        self.step += 1
        return self.encoder.build(list(self.client.q), self.client.objects, self.step/30, self.step)


class TransientClient(Client):
    """Moves another station briefly and restores it before replying."""

    def command(self, name, args):
        if name == 'demo' and self.transient is not None and not getattr(self, 'fired', False):
            self.fired = True
            self.transient.q = [.2]+[0.]*42
            time.sleep(0.1)
            self.transient.q = [0.]*43
        return super().command(name, args)


def stations(count=2, kind='synthetic', client=Client):
    configurations = [config(i) for i in range(1, count+1)]
    clients = {v['station_id']: client(v) for v in configurations}
    for c in clients.values():
        c.encoder = StateEncoder(c.registry, source_kind=kind)
    return configurations, clients


def test_continuous_trace_passes_with_full_coverage_and_no_excursion():
    configurations, clients = stations()
    samplers = {k: Sampler(v) for k, v in clients.items()}
    events, rows = [], []
    report = run_cross_talk(configurations, clients, events.append, trace_samplers=samplers,
                            trace_policy=policy(), trace_sample_sink=rows.append)
    assert report['passed'] and report['trace_passed'] and report['continuous_trace_complete']
    assert report['evidence_scope'] == 'endpoint_snapshots_and_continuous_trace'
    assert report['trace_coverage_overall'] == 1.0 and report['trace_tolerance_exceedances'] == 0
    assert report['trace_windows'] == report['cases'] and report['trace_window_station_pairs'] == report['cases']
    assert report['undetectable_excursion_max_s'] == 0.25 and report['trace_policy_sha256'] == policy_sha256(policy())
    assert rows and all(r['discrete_mismatches'] == 0 for r in rows)
    assert all('trace' in e for e in events if e['kind'] == 'cross_talk_case')


def test_transient_excursion_missed_by_snapshots_is_caught_by_trace():
    configurations, clients = stations(client=TransientClient)
    clients['engineering-1'].transient = clients['engineering-2']
    samplers = {k: Sampler(v) for k, v in clients.items()}
    events = []
    report = run_cross_talk(configurations, clients, events.append, trace_samplers=samplers, trace_policy=policy())
    assert not report['passed'] and report['trace_exceedance_stopped']
    assert report['changed_other_count'] == 0  # Endpoint snapshots saw nothing.
    assert report['trace_tolerance_exceedances'] >= 1
    assert report['trace_max_observed']['joint_rad'] == pytest.approx(0.2)
    last = events[-1]
    assert last['command'] == 'demo' and last['changed_stations'] == []
    assert last['trace']['stations'][0]['station_id'] == 'engineering-2'


def test_coverage_gap_keeps_report_failed_without_inventing_cross_talk():
    configurations, clients = stations()
    samplers = {k: Sampler(v) for k, v in clients.items()}
    samplers['engineering-2'] = Sampler(clients['engineering-2'], stall_on=40, stall_s=0.4)
    report = run_cross_talk(configurations, clients, lambda event: None, trace_samplers=samplers, trace_policy=policy())
    assert report['snapshot_contract_passed'] and report['trace_tolerance_exceedances'] == 0
    assert not report['trace_passed'] and not report['passed']
    assert report['trace_windows_below_min_coverage'] >= 1 and report['trace_min_window_coverage'] < 1.0


def test_live_snapshots_pass_only_with_a_complete_trace_and_other_evidence_stays_open():
    configurations, clients = stations(kind='live')
    report = run_cross_talk(configurations, clients, lambda event: None, trace_samplers={
        k: Sampler(v) for k, v in clients.items()}, trace_policy=policy())
    assert report['source_kind'] == 'live' and report['passed'] and report['trace_passed']
    assert not report['packet_capture_complete'] and not report['one_hour_capacity_run_complete']
    assert not report['wrong_station_connection_authentication_tested']


def test_trace_requires_independent_samplers_and_a_policy():
    configurations, clients = stations()
    with pytest.raises(ValueError, match='independent'):
        run_cross_talk(configurations, clients, lambda e: None, trace_samplers=dict(clients), trace_policy=policy())
    with pytest.raises(ValueError, match='declared policy'):
        run_cross_talk(configurations, clients, lambda e: None, trace_samplers={k: Sampler(v) for k, v in clients.items()})


class FakeConnection:
    """Stands in for a websockets sync connection; no socket is opened."""

    def __init__(self, frames=(), reply=None):
        self.frames, self.reply, self.sent, self.closed = list(frames), reply, [], threading.Event()

    def __iter__(self):
        for frame in self.frames:
            yield json.dumps(frame)
        self.closed.wait(5)

    def send(self, raw):
        self.sent.append(json.loads(raw))

    def recv(self, timeout=None):
        return json.dumps(self.reply(self.sent[-1]))

    def close(self):
        self.closed.set()


def test_live_adapters_validate_frames_and_bind_replies_to_requests(tmp_path):
    from isaac.stations.live import PrivateCommandClient, PublicStateReader, endpoint_target, load_registry
    value = config(1)
    client = Client(value)
    good = client.public_state()
    reader = PublicStateReader({'unix': '/run/acoustic-vocab/engineering-1/state.sock'}, client.registry,
                               connect=lambda *a, **k: FakeConnection([good]))
    assert reader.public_state() == good
    reader.close()
    other = Client(config(2)).public_state()
    wrong = PublicStateReader({'tcp_port': 20002}, client.registry, connect=lambda *a, **k: FakeConnection([other]))
    with pytest.raises(RuntimeError, match='Wrong station'):
        wrong.public_state()
    wrong.close()
    commands = PrivateCommandClient({'tcp_port': 20003}, 'f'*32, connect=lambda *a, **k: FakeConnection(
        reply=lambda request: dict(request_id=request['request_id'], accepted=True)))
    assert commands.command('health', {})['accepted'] is True
    assert commands.connection.sent[0]['control_session_id'] == 'f'*32
    mismatched = PrivateCommandClient({'tcp_port': 20003}, 'f'*32, connect=lambda *a, **k: FakeConnection(
        reply=lambda request: dict(request_id='0'*32, accepted=True)))
    with pytest.raises(RuntimeError, match='does not match'):
        mismatched.command('health', {})
    for endpoint in ({'unix': 'relative.sock'}, {'tcp_port': 80}, {'unix': '/a', 'tcp_port': 20000}, {}):
        with pytest.raises(ValueError):
            endpoint_target(endpoint)
    registry = dict(station_id=value['station_id'], scene_sha256='a'*64, reset_snapshot_sha256='b'*64,
                    joint_names=list(client.registry.joint_names), object_states=[['fixture_object', []]], anchor_ids=[])
    path = tmp_path/'registry.json'
    path.write_text(json.dumps(registry))
    assert load_registry(path, hashlib.sha256(path.read_bytes()).hexdigest()) == client.registry
    with pytest.raises(ValueError, match='Registry hash'):
        load_registry(path, '0'*64)


def test_sampler_failure_marks_trace_incomplete():
    configurations, clients = stations()
    samplers = {k: Sampler(v) for k, v in clients.items()}
    broken = samplers['engineering-2']
    original = broken.public_state
    def fail_later():
        if broken.calls > 30:
            raise RuntimeError('connection lost')
        return original()
    broken.public_state = fail_later
    report = run_cross_talk(configurations, clients, lambda e: None, trace_samplers=samplers, trace_policy=policy())
    assert not report['continuous_trace_complete'] and not report['passed']
    assert 'connection lost' in report['trace_errors']['engineering-2']
