"""Live adapters and operator CLI for the cross-talk audit with continuous trace.

Endpoints are explicit per station: ``{"unix": "/abs/path"}`` for a station
gateway socket (the auditing process must then run as that station's UID; the
gateway refuses every other UID, including root) or ``{"tcp_port": N}`` for an
operator-run loopback relay (``isaac.e2e.relay``) in front of that socket.
Each station gets two independent public connections: one for the command
matrix endpoint snapshots and one for the continuous trace sampler. Uses the
approved websockets 12 synchronous client. Nothing is discovered or rebound.

    python -m isaac.stations.live --clients <private>/clients.json --policy <private>/trace-policy.json \
        --policy-sha256 <hash> --output <private>/cross-talk-<date>
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid

from isaac.publisher.protocol import PublicRegistry, strict_loads, validate_frame
from .config import load
from .trace import policy_sha256, validate_policy

CLIENT_FIELDS = {'config_file', 'config_sha256', 'registry_file', 'registry_sha256', 'control_session_id',
                 'public', 'private'}


def endpoint_target(endpoint):
    """Validate one explicit endpoint: ('unix', absolute path) or ('tcp', loopback port)."""
    if not isinstance(endpoint, dict) or len(endpoint) != 1:
        raise ValueError('Exactly one explicit endpoint kind required')
    if 'unix' in endpoint:
        if not isinstance(endpoint['unix'], str) or not endpoint['unix'].startswith('/'):
            raise ValueError('Absolute station gateway socket required')
        return 'unix', endpoint['unix']
    port = endpoint.get('tcp_port')
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Explicit loopback relay port required')
    return 'tcp', port


def _connect(endpoint, path, **kwargs):
    kind, target = endpoint_target(endpoint)
    from websockets.sync.client import connect, unix_connect
    if kind == 'unix':
        return unix_connect(target, uri='ws://localhost'+path, compression=None, **kwargs)
    return connect(f'ws://127.0.0.1:{target}{path}', compression=None, **kwargs)


def load_registry(path, expected_sha256):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Registry hash mismatch')
    value = strict_loads(raw)
    return PublicRegistry(value['station_id'], value['scene_sha256'], value['reset_snapshot_sha256'],
                          tuple(value['joint_names']),
                          tuple((key, tuple(fields)) for key, fields in value['object_states']),
                          tuple(value['anchor_ids']))


class PublicStateReader:
    """Keeps the latest strictly validated frame from one public connection."""

    def __init__(self, endpoint, registry, *, max_age_s=1.0, connect=_connect):
        self.registry, self.max_age_ns = registry, int(max_age_s*1e9)
        self.lock, self.latest, self.received_ns, self.error = threading.Lock(), None, None, None
        self.connection = connect(endpoint, '/state', max_size=1048576)
        self.thread = threading.Thread(target=self._run, daemon=True, name='public-reader-'+registry.station_id)
        self.thread.start()

    def _run(self):
        try:
            for raw in self.connection:
                frame = validate_frame(strict_loads(raw), self.registry)
                with self.lock:
                    self.latest, self.received_ns = frame, time.monotonic_ns()
        except Exception as error:
            with self.lock:
                self.error = type(error).__name__+': '+str(error)

    def public_state(self, wait_s=2.0):
        deadline = time.monotonic()+wait_s
        while True:
            with self.lock:
                if self.error:
                    raise RuntimeError('Public reader failed: '+self.error)
                if self.latest is not None and time.monotonic_ns()-self.received_ns <= self.max_age_ns:
                    return deepcopy(self.latest)
            if time.monotonic() > deadline:
                raise RuntimeError('No fresh public frame')
            time.sleep(.005)

    def close(self):
        self.connection.close()
        self.thread.join(timeout=5)


class PrivateCommandClient:
    def __init__(self, endpoint, control_session_id, *, timeout_s=60.0, connect=_connect):
        if not isinstance(control_session_id, str) or len(control_session_id) != 32:
            raise ValueError('Station control session id required')
        self.control_session_id, self.timeout_s = control_session_id, timeout_s
        self.connection = connect(endpoint, '/commands', max_size=65536)

    def command(self, name, args):
        request = dict(version=1, kind='private_command', control_session_id=self.control_session_id,
                       request_id=uuid.uuid4().hex, command=name, args=args)
        self.connection.send(json.dumps(request))
        reply = strict_loads(self.connection.recv(timeout=self.timeout_s))
        if not isinstance(reply, dict) or reply.get('request_id') != request['request_id']:
            raise RuntimeError('Private reply does not match request')
        return reply

    def close(self):
        self.connection.close()


class LiveStation:
    def __init__(self, registry, reader, commands):
        self.registry, self.reader, self.commands = registry, reader, commands

    def public_state(self):
        return self.reader.public_state()

    def command(self, name, args):
        return self.commands.command(name, args)


class DurableJsonl:
    def __init__(self, path):
        self.stream, self.lock = Path(path).open('x', encoding='utf-8', newline='\n'), threading.Lock()

    def __call__(self, row):
        with self.lock:
            self.stream.write(json.dumps(row, sort_keys=True, allow_nan=False)+'\n')
            self.stream.flush()
            os.fsync(self.stream.fileno())

    def close(self):
        self.stream.close()


def main(argv=None):
    from .audit import run_cross_talk
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--clients', type=Path, required=True)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--policy-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    policy = validate_policy(strict_loads(args.policy.read_bytes()))
    if policy_sha256(policy) != args.policy_sha256:
        raise ValueError('Declared trace policy hash mismatch')
    entries = strict_loads(args.clients.read_bytes())
    if not isinstance(entries, list) or any(not isinstance(e, dict) or set(e) != CLIENT_FIELDS for e in entries):
        raise ValueError('Exact client manifest fields required')
    args.output.mkdir(parents=True, exist_ok=False)
    configurations, clients, samplers, closers = [], {}, {}, []
    events = DurableJsonl(args.output/'events.jsonl')
    samples_stream = (args.output/'trace-samples.csv').open('x', newline='', encoding='utf-8')
    fields = ['station_id', 'receipt_ns', 'seq', 'session_id', 'joint_rad', 'position_m', 'rotation_rad',
              'visual_scalar', 'discrete_mismatches']
    writer = csv.DictWriter(samples_stream, fieldnames=fields)
    writer.writeheader()
    try:
        for entry in entries:
            value = load(entry['config_file'], entry['config_sha256'])
            registry = load_registry(entry['registry_file'], entry['registry_sha256'])
            configurations.append(value)
            reader = PublicStateReader(entry['public'], registry); closers.append(reader)
            commands = PrivateCommandClient(entry['private'], entry['control_session_id']); closers.append(commands)
            sampler = PublicStateReader(entry['public'], registry); closers.append(sampler)
            clients[value['station_id']] = LiveStation(registry, reader, commands)
            samplers[value['station_id']] = sampler
        report = run_cross_talk(configurations, clients, events, trace_samplers=samplers, trace_policy=policy,
                                trace_sample_sink=writer.writerow, clock_ns=time.perf_counter_ns)
        report.update(scope='SIMULATION_TEST', participant=False, network_review_complete=False)
    finally:
        for resource in reversed(closers):
            try: resource.close()
            except Exception: pass
        events.close()
        samples_stream.flush(); os.fsync(samples_stream.fileno())
        samples_stream.close()
    report['trace_samples_sha256'] = hashlib.sha256((args.output/'trace-samples.csv').read_bytes()).hexdigest()
    report['events_sha256'] = hashlib.sha256((args.output/'events.jsonl').read_bytes()).hexdigest()
    with (args.output/'report.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.flush(); os.fsync(stream.fileno())
    print(json.dumps(dict(passed=report['passed'], trace_passed=report['trace_passed'],
                          trace_coverage_overall=report['trace_coverage_overall'])))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
