"""Deterministic synthetic non-study soak schedule and bounded private-command driver.

The schedule is a pinned, canonical, seeded function of its parameters. The
driver sends only scheduled private commands, one at a time, at a bounded rate,
over the existing private command API. Intent is fsynced before every send and
every reply is retained verbatim. Unknown or malformed replies are refused and
halt the run; nothing is retried. Unity-side inputs (block context, trial and
dummy-response requests, command acknowledgements) are appended to a separate
hash-chained feed for the joined Unity host.

Faults are injected only through an explicitly authorized hook. The driver never
resumes an exposure and decides no outcome: normalized evidence still goes to
``isaac.soak.analyze``.
"""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import random
import time
from urllib.parse import urlsplit
import uuid

from isaac.commands.protocol import LEGAL_PAIRS, MODES, decode, validate
from isaac.reset.snapshot import canonical_bytes
from .analyze import FAULT_TYPES, load_json, require
from .collect import encoded
from .native import GUID, ID, SHA, read_bounded, safe_path

GENERATOR = 'isaac.soak.driver/1'
PAIRS = tuple(sorted(LEGAL_PAIRS))
RESPONSE_CODES = ('commit', 'dont_know', 'timeout')
COMMAND_OPS = ('set_mode', 'reset', 'demo', 'lock_probe')
UNITY_OPS = ('block_begin', 'trial', 'dummy_response')
MIN_COMMAND_INTERVAL_S = 2
MAX_COMMANDS_PER_MINUTE = 20
JOURNAL_LIMIT = 256 * 1024 * 1024
REPLY_LIMIT = 65536
# Every reason the private dispatcher, queue and transport can emit. Anything
# else is an unknown reply and is refused rather than interpreted.
REASONS = frozenset((
    'MALFORMED', 'UNKNOWN_CLIENT', 'PROTECTED_TARGET_COMMAND', 'CONTROL_SESSION_MISMATCH',
    'REQUEST_ID_CONFLICT', 'REQUEST_IN_PROGRESS', 'IDEMPOTENCY_CAPACITY', 'FAULT_LATCHED',
    'NOT_READY', 'DEMO_NOT_IMPLEMENTED', 'RESET_REQUIRED', 'DEMO_START_FAILED', 'MODE_CHANGED',
    'RESET_FAILED', 'DEMO_ACTIVE', 'RESET_COMPLETE', 'PAUSED_AT_SAFE_POINT',
    'STOPPED_RESTART_REQUIRED', 'RESUMED_EXPLICITLY', 'STOPPED', 'STOP_RESET_FAILED', 'HEALTH',
    'DEMO_COMPLETE', 'EXECUTION_FAILED', 'COMMAND_FAILED', 'PROTECTED_MODE_INTERRUPTED',
    'RESET_INTERRUPTED', 'STOP_INTERRUPTED', 'CAPACITY_INTERRUPTED', 'COMMAND_QUEUE_FULL',
    'PROTECTED_BOUNDARY_SUPERSEDED', 'SERVICE_STOPPING'))
REPLY_KEYS = frozenset(('version', 'kind', 'request_id', 'accepted', 'reason', 'mode', 'host_mono_ms',
                        'sim_time', 'reset_ok', 'duplicate', 'health'))
HEALTH_KEYS = frozenset(('control_session_id', 'mode', 'paused', 'stopped', 'fault', 'demo_active',
                         'publisher_ready', 'neutral_verification_age_ms', 'publisher_age_ms',
                         'health_sample_host_mono_ms', 'exposure_ready', 'public_stream_recovered'))
PROBE_REPLY_KEYS = frozenset(('version', 'kind', 'control_session_id', 'request_id', 'accepted', 'reason', 'health'))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


# --------------------------------------------------------------------------
# Schedule
# --------------------------------------------------------------------------

def _rng(station_id, seed):
    material = f'{GENERATOR}\n{station_id}\n{seed}'.encode('utf-8')
    return random.Random(int.from_bytes(hashlib.sha256(material).digest()[:8], 'big'))


def build_schedule(*, station_id, seed, seconds, block_seconds=300, trial_interval_s=20, fault_types=()):
    """Return the canonical schedule document for these exact parameters."""
    require(isinstance(station_id, str) and ID.fullmatch(station_id), 'Schedule station identity')
    require(type(seed) is int and 0 <= seed < 2**63, 'Schedule seed must be a nonnegative integer')
    require(type(seconds) is int and 60 <= seconds <= 36000, 'Schedule duration')
    require(type(block_seconds) is int and 60 <= block_seconds <= 3600, 'Block duration')
    require(type(trial_interval_s) is int and 15 <= trial_interval_s <= block_seconds - 15, 'Trial interval')
    fault_types = list(fault_types)
    require(all(isinstance(x, str) and x in FAULT_TYPES for x in fault_types) and
            len(set(fault_types)) == len(fault_types), 'Fault slots must be distinct known fault types')
    blocks = seconds // block_seconds
    protected = [index for index in range(blocks) if index % 2 == 1]
    require(protected, 'Schedule needs at least one protected block')
    # Faults never land in the first protected block, so a committed response
    # exists before every planned fault; distinct blocks are guaranteed.
    require(not fault_types or len(protected) >= len(fault_types) + 1, 'Too few protected blocks for fault slots')
    fault_blocks = {protected[(j + 1) * len(protected) // (len(fault_types) + 1)]: kind
                    for j, kind in enumerate(fault_types)}
    rng = _rng(station_id, seed)
    steps = []

    def add(t, op, block, block_id, **fields):
        steps.append(dict(index=len(steps), t_s=t, op=op, block=block, block_id=block_id, **fields))

    for block_index in range(blocks):
        block = 'teaching' if block_index % 2 == 0 else 'protected'
        block_id = f'block-{block_index:04d}'
        start = block_index * block_seconds
        add(start, 'block_begin', block, block_id)
        add(start, 'set_mode', block, block_id, mode='teaching' if block == 'teaching' else 'test')
        t, trial = start + 3, 0
        while t + 10 <= start + block_seconds:
            trial_id = f'{block_id}-trial-{trial:03d}'
            add(t, 'reset', block, block_id, trial_id=trial_id)
            add(t + 2, 'trial', block, block_id, trial_id=trial_id)
            if block == 'teaching':
                action, target = PAIRS[rng.randrange(len(PAIRS))]
                add(t + 4, 'demo', block, block_id, trial_id=trial_id, action=action, target=target)
            else:
                if trial == 0 and block_index in fault_blocks:
                    add(t + 3, 'fault_slot', block, block_id, trial_id=trial_id, fault_type=fault_blocks[block_index])
                code = RESPONSE_CODES[rng.randrange(len(RESPONSE_CODES))]
                add(t + 4, 'dummy_response', block, block_id, trial_id=trial_id, response_code=code)
                action, target = PAIRS[rng.randrange(len(PAIRS))]
                add(t + 6, 'lock_probe', block, block_id, trial_id=trial_id, action=action, target=target)
            t += trial_interval_s + rng.randrange(3)
            trial += 1
    document = dict(version=1, kind='soak_synthetic_schedule', generator=GENERATOR, scope='synthetic_nonstudy',
                    participants=False, station_id=station_id, seed=seed, seconds=seconds,
                    block_seconds=block_seconds, trial_interval_s=trial_interval_s, fault_types=fault_types,
                    min_command_interval_s=MIN_COMMAND_INTERVAL_S, max_commands_per_minute=MAX_COMMANDS_PER_MINUTE,
                    steps=steps)
    check_steps(document)
    return document


STEP_FIELDS = {
    'block_begin': set(), 'set_mode': {'mode'}, 'reset': {'trial_id'}, 'trial': {'trial_id'},
    'demo': {'trial_id', 'action', 'target'}, 'dummy_response': {'trial_id', 'response_code'},
    'lock_probe': {'trial_id', 'action', 'target'}, 'fault_slot': {'trial_id', 'fault_type'},
}


def check_steps(document):
    """Independent semantic gate: demos only in teaching mode, probes only in test."""
    steps = document['steps']
    require(isinstance(steps, list) and steps, 'Schedule steps')
    mode, block, block_id = 'test', None, None   # The dispatcher starts in test.
    reset_for, trial_seen, probed, protected = None, set(), set(), set()
    commands, last_command, faults = deque(), None, []
    previous = 0
    for index, step in enumerate(steps):
        require(isinstance(step, dict) and step.get('op') in STEP_FIELDS, 'Unknown schedule step')
        require(set(step) == {'index', 't_s', 'op', 'block', 'block_id'} | STEP_FIELDS[step['op']], 'Schedule step fields')
        require(step['index'] == index and type(step['t_s']) is int and previous <= step['t_s'] <= document['seconds'],
                'Schedule order')
        previous = step['t_s']
        op = step['op']
        if op == 'block_begin':
            require(step['block'] in ('teaching', 'protected') and ID.fullmatch(step['block_id']) and
                    step['block_id'] != block_id, 'Block identity')
            block, block_id, reset_for = step['block'], step['block_id'], None
            require(index + 1 < len(steps) and steps[index + 1]['op'] == 'set_mode', 'Block must begin with set_mode')
            if block == 'protected':
                protected.add(block_id)
        require(step['block'] == block and step['block_id'] == block_id, 'Step outside its block')
        if op == 'set_mode':
            require(steps[index - 1]['op'] == 'block_begin' and
                    step['mode'] == ('teaching' if block == 'teaching' else 'test'), 'Block mode')
            mode = step['mode']
        elif op == 'reset':
            require(step['trial_id'] not in trial_seen, 'Duplicate trial reset')
            reset_for = step['trial_id']
        elif op == 'trial':
            require(reset_for == step['trial_id'] and step['trial_id'] not in trial_seen, 'Trial lacks a fresh reset')
            trial_seen.add(step['trial_id'])
        elif op == 'demo':
            require(block == 'teaching' and mode == 'teaching' and (step['action'], step['target']) in LEGAL_PAIRS,
                    'Demo outside a teaching block')
        elif op == 'lock_probe':
            require(block == 'protected' and mode == 'test' and (step['action'], step['target']) in LEGAL_PAIRS,
                    'Lock probe outside a protected block')
            probed.add(block_id)
        elif op == 'dummy_response':
            require(block == 'protected' and mode == 'test' and step['response_code'] in RESPONSE_CODES,
                    'Dummy response outside a protected block')
        elif op == 'fault_slot':
            require(block == 'protected' and step['fault_type'] in document['fault_types'], 'Fault slot placement')
            faults.append(step['fault_type'])
        if op in ('trial', 'demo', 'lock_probe', 'dummy_response', 'fault_slot'):
            require(step['trial_id'] in trial_seen or op == 'trial', 'Step before its trial')
        if op in COMMAND_OPS:
            t = step['t_s']
            require(last_command is None or t - last_command >= document['min_command_interval_s'], 'Command rate')
            last_command = t
            commands.append(t)
            while commands[0] <= t - 60:
                commands.popleft()
            require(len(commands) <= document['max_commands_per_minute'], 'Command rate')
    require(protected and protected <= probed, 'Every protected block needs a lock probe')
    require(sorted(faults) == sorted(document['fault_types']), 'Every planned fault needs one slot')
    return document


def load_schedule(raw, pin):
    """Accept only the exact canonical bytes the generator emits for the pin."""
    require(isinstance(raw, bytes) and 0 < len(raw) <= 16 * 1024 * 1024, 'Schedule size')
    require(isinstance(pin, str) and SHA.fullmatch(pin) and sha(raw) == pin, 'Schedule pin mismatch')
    document = load_json(raw)
    require(isinstance(document, dict) and set(document) == {
        'version', 'kind', 'generator', 'scope', 'participants', 'station_id', 'seed', 'seconds', 'block_seconds',
        'trial_interval_s', 'fault_types', 'min_command_interval_s', 'max_commands_per_minute', 'steps'},
        'Schedule fields')
    require(document['version'] == 1 and document['kind'] == 'soak_synthetic_schedule' and
            document['generator'] == GENERATOR and document['scope'] == 'synthetic_nonstudy' and
            document['participants'] is False, 'Nonstudy schedule required')
    rebuilt = build_schedule(station_id=document['station_id'], seed=document['seed'], seconds=document['seconds'],
                             block_seconds=document['block_seconds'], trial_interval_s=document['trial_interval_s'],
                             fault_types=document['fault_types'])
    require(canonical_bytes(rebuilt) == raw, 'Schedule is not the deterministic generator output')
    return rebuilt


# --------------------------------------------------------------------------
# Append-only hash chains
# --------------------------------------------------------------------------

def read_chain(raw, required):
    """Verify exact compact rows; a torn tail is refused for operator review."""
    require(len(raw) <= JOURNAL_LIMIT, 'Journal exceeds limit')
    require(not raw or raw.endswith(b'\n'), 'Torn journal tail requires operator review')
    rows, previous = [], '0' * 64
    for line in raw.splitlines(keepends=True):
        row = load_json(line)
        require(isinstance(row, dict) and set(row) == required | {'seq', 'previous_sha256', 'sha256'}, 'Journal fields')
        digest = row.pop('sha256')
        require(row['seq'] == len(rows) and row['previous_sha256'] == previous and
                sha(encoded(row)) == digest, 'Journal chain')
        row['sha256'] = digest
        require(encoded(row) == line, 'Journal bytes are not canonical')
        rows.append(row)
        previous = digest
    return rows


class HashChain:
    def __init__(self, path, fields, *, resume=False):
        self.path = safe_path(path, missing=not resume)
        self.fields = frozenset(fields)
        if resume:
            self.rows = read_chain(read_bounded(self.path, JOURNAL_LIMIT), self.fields)
            self.stream = self.path.open('ab')
        else:
            self.rows = []
            self.stream = self.path.open('xb')
        self.previous = self.rows[-1]['sha256'] if self.rows else '0' * 64

    def append(self, **values):
        require(set(values) == self.fields, 'Journal row fields')
        row = dict(values, seq=len(self.rows), previous_sha256=self.previous)
        row['sha256'] = sha(encoded(row))
        self.stream.write(encoded(row))
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.rows.append(row)
        self.previous = row['sha256']
        return row

    def close(self):
        self.stream.close()


JOURNAL_FIELDS = ('version', 'clock_domain', 'segment', 'monotonic_ns', 'kind', 'payload')
FEED_FIELDS = ('version', 'schedule_sha256', 'station_id', 'kind', 'payload')


# --------------------------------------------------------------------------
# Reply validation
# --------------------------------------------------------------------------

def finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def check_health(health, session):
    require(isinstance(health, dict) and set(health) == HEALTH_KEYS, 'REPLY_HEALTH_SHAPE')
    require(health['control_session_id'] == session and health['mode'] in MODES, 'REPLY_HEALTH_SESSION')
    require(all(type(health[k]) is bool for k in ('paused', 'stopped', 'demo_active', 'publisher_ready',
                                                   'exposure_ready')) and health['public_stream_recovered'] is False,
            'REPLY_HEALTH_TYPES')
    require(health['fault'] is None or isinstance(health['fault'], str), 'REPLY_HEALTH_FAULT')
    require(finite(health['health_sample_host_mono_ms']) and
            all(health[k] is None or finite(health[k]) for k in ('neutral_verification_age_ms', 'publisher_age_ms')),
            'REPLY_HEALTH_CLOCK')


def check_reply(raw, request):
    """Strictly decode one reply bound to its request; refuse anything else."""
    require(isinstance(raw, str) and len(raw.encode('utf-8')) <= REPLY_LIMIT, 'REPLY_NOT_TEXT')
    try:
        reply = decode(raw)
    except (ValueError, TypeError, RecursionError) as error:
        raise ValueError('REPLY_MALFORMED') from error
    require(isinstance(reply, dict) and set(reply) == REPLY_KEYS, 'REPLY_SHAPE')
    require(type(reply['version']) is int and reply['version'] == 1 and reply['kind'] == 'private_reply', 'REPLY_VERSION')
    require(reply['request_id'] == request['request_id'], 'REPLY_REQUEST_BINDING')
    require(type(reply['accepted']) is bool and type(reply['duplicate']) is bool, 'REPLY_TYPES')
    require(isinstance(reply['reason'], str) and reply['reason'] in REASONS, 'REPLY_UNKNOWN_REASON')
    require(reply['mode'] in MODES and finite(reply['host_mono_ms']) and finite(reply['sim_time']), 'REPLY_MODE_CLOCK')
    require(reply['reset_ok'] is None or type(reply['reset_ok']) is bool, 'REPLY_RESET_TYPE')
    # Every request is new; a duplicate means someone else reused the ID.
    require(reply['duplicate'] is False, 'REPLY_UNEXPECTED_DUPLICATE')
    require(reply['reason'] != 'RESET_COMPLETE' or (reply['accepted'] and reply['reset_ok'] is True), 'REPLY_INCONSISTENT')
    require(reply['reason'] != 'RESET_FAILED' or (not reply['accepted'] and reply['reset_ok'] is False), 'REPLY_INCONSISTENT')
    require(reply['reason'] != 'PROTECTED_TARGET_COMMAND' or (not reply['accepted'] and reply['mode'] == 'test'),
            'REPLY_INCONSISTENT')
    check_health(reply['health'], request['control_session_id'])
    return reply


def check_health_reply(raw, request):
    require(isinstance(raw, str) and len(raw.encode('utf-8')) <= REPLY_LIMIT, 'REPLY_NOT_TEXT')
    try:
        reply = decode(raw)
    except (ValueError, TypeError, RecursionError) as error:
        raise ValueError('REPLY_MALFORMED') from error
    require(isinstance(reply, dict) and set(reply) == PROBE_REPLY_KEYS, 'PROBE_REPLY_SHAPE')
    require(type(reply['version']) is int and reply['version'] == 1 and reply['kind'] == 'private_health_reply' and
            reply['request_id'] == request['request_id'] and
            reply['control_session_id'] == request['control_session_id'], 'PROBE_REPLY_BINDING')
    require(reply['accepted'] is True and reply['reason'] == 'HEALTH', 'PROBE_REFUSED')
    check_health(reply['health'], request['control_session_id'])
    return reply


def classify(step, reply, *, allow_missing_demo_content=False):
    """Map a well-formed reply to the schedule's expectation; never to a verdict."""
    op, ok = step['op'], reply['accepted']
    if op == 'set_mode':
        good = ok and reply['reason'] == 'MODE_CHANGED' and reply['mode'] == step['mode']
        if step['mode'] == 'test':
            good = good and reply['reset_ok'] is True
    elif op == 'reset':
        good = ok and reply['reason'] == 'RESET_COMPLETE' and reply['reset_ok'] is True and \
            reply['mode'] == ('teaching' if step['block'] == 'teaching' else 'test')
    elif op == 'demo':
        if not ok and reply['reason'] == 'DEMO_NOT_IMPLEMENTED' and allow_missing_demo_content:
            return 'demo_content_missing'
        good = ok and reply['reason'] == 'DEMO_COMPLETE' and reply['mode'] == 'teaching'
    else:
        good = (not ok and reply['reason'] == 'PROTECTED_TARGET_COMMAND' and reply['mode'] == 'test'
                and reply['reset_ok'] is None)
    return 'expected' if good else 'unexpected'


# --------------------------------------------------------------------------
# Clients and hooks
# --------------------------------------------------------------------------

class WebSocketCommandClient:
    """One private WebSocket on an explicit loopback or Unix endpoint (#57)."""

    def __init__(self, *, endpoint=None, unix_socket=None, open_timeout=5.0):
        require((endpoint is None) != (unix_socket is None), 'Choose exactly one private endpoint')
        if endpoint is not None:
            parts = urlsplit(endpoint)
            require(parts.scheme == 'ws' and parts.path == '/commands' and not parts.query and not parts.fragment
                    and parts.username is None and parts.port is not None, 'Private ws://loopback:port/commands required')
            require(ipaddress.ip_address(parts.hostname).is_loopback, 'Private endpoint must be loopback')
        else:
            require(Path(unix_socket).is_absolute(), 'Absolute Unix socket path required')
        self.endpoint, self.unix_socket, self.open_timeout = endpoint, unix_socket, open_timeout
        self.socket = None

    def connect(self):
        from websockets.sync.client import connect, unix_connect
        options = dict(open_timeout=self.open_timeout, max_size=REPLY_LIMIT, compression=None, close_timeout=2)
        if self.endpoint is not None:
            self.socket = connect(self.endpoint, **options)
        else:
            self.socket = unix_connect(str(self.unix_socket), uri='ws://localhost/commands', **options)

    def exchange(self, text, timeout):
        if self.socket is None:
            self.connect()
        self.socket.send(text)
        return self.socket.recv(timeout=timeout)

    def reconnect(self):
        self.close()
        self.connect()

    def close(self):
        if self.socket is not None:
            try:
                self.socket.close()
            finally:
                self.socket = None


def read_operator_file(path, fields):
    raw = read_bounded(path, 4096)
    value = load_json(raw)
    require(isinstance(value, dict) and set(value) == set(fields) and type(value.get('version')) is int and
            value['version'] == 1, 'Operator file fields')
    return value, sha(raw)


class OperatorFileFaultHook:
    """Physical/process faults stay manual: request, then await operator confirmation."""

    def __init__(self, directory, *, timeout_s=900, clock=time.monotonic_ns, sleep=time.sleep):
        self.directory, self.timeout_s, self.clock, self.sleep = Path(directory), timeout_s, clock, sleep

    def __call__(self, request):
        path = self.directory / f"fault-request-{request['fault_id']}.json"
        safe_path(path, missing=True)
        with path.open('xb') as stream:
            stream.write(canonical_bytes(dict(version=1, **request)))
            stream.flush()
            os.fsync(stream.fileno())
        done = self.directory / f"fault-done-{request['fault_id']}.json"
        deadline = self.clock() + int(self.timeout_s * 1e9)
        while not done.exists():
            require(self.clock() <= deadline, 'Operator fault confirmation timeout')
            self.sleep(1.0)
        value, digest = read_operator_file(done, ('version', 'fault_id', 'fault_type', 'performed'))
        require(value['fault_id'] == request['fault_id'] and value['fault_type'] == request['fault_type'] and
                value['performed'] is True, 'Operator fault confirmation differs')
        return dict(method='operator_file', confirmation_sha256=digest)


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

class Halt(Exception):
    pass


class SoakDriver:
    def __init__(self, schedule_raw, schedule_sha256, output, client, *, control_session_id, resume=False,
                 clock=time.monotonic_ns, sleep=time.sleep, reply_timeout_s=10.0, demo_timeout_s=120.0,
                 fault_hook=None, authorize_fault_injection=False, recovery_timeout_s=1800,
                 allow_missing_demo_content=False):
        self.schedule = load_schedule(schedule_raw, schedule_sha256)
        self.schedule_sha256 = schedule_sha256
        require(isinstance(control_session_id, str) and GUID.fullmatch(control_session_id), 'Pinned control session')
        require((fault_hook is None) or authorize_fault_injection, 'Fault hook requires explicit authorization')
        require(not authorize_fault_injection or callable(fault_hook), 'Authorized fault injection requires a hook')
        require(0 < reply_timeout_s <= 60 and 0 < demo_timeout_s <= 600 and 0 < recovery_timeout_s <= 7200,
                'Driver timeout range')
        self.output = safe_path(output, directory=True, missing=not resume)
        if resume:
            require(self.output.is_dir(), 'Resume requires the existing driver output')
        else:
            require(not self.output.exists(), 'Fresh driver output required')
            self.output.mkdir(parents=True)
            (self.output / 'operator').mkdir()
        self.client, self.session, self.clock, self.sleep = client, control_session_id, clock, sleep
        self.reply_timeout_s, self.demo_timeout_s, self.recovery_timeout_s = reply_timeout_s, demo_timeout_s, recovery_timeout_s
        self.fault_hook = fault_hook if authorize_fault_injection else None
        self.allow_missing_demo_content = allow_missing_demo_content
        self.resume = resume
        self.segment = uuid.uuid4().hex
        self.journal = HashChain(self.output / 'driver-journal.jsonl', JOURNAL_FIELDS, resume=resume)
        try:
            self.feed = HashChain(self.output / 'unity-inputs.jsonl', FEED_FIELDS, resume=resume)
        except Exception:
            self.journal.close()
            raise
        self.mode = None if resume else 'test'
        self.resets, self.sends = {}, deque()
        self.last_send = None
        self.halted = None

    # Journal helpers ----------------------------------------------------
    def write(self, kind, payload):
        return self.journal.append(version=1, clock_domain='driver_monotonic_ns', segment=self.segment,
                                   monotonic_ns=str(self.clock()), kind=kind, payload=payload)

    def publish(self, kind, payload):
        row = self.feed.append(version=1, schedule_sha256=self.schedule_sha256,
                               station_id=self.schedule['station_id'], kind=kind, payload=payload)
        self.write('unity_input', dict(feed_seq=row['seq'], feed_sha256=row['sha256'], kind=kind,
                                       step_index=payload['step_index']))
        return row

    def halt(self, code, **details):
        self.halted = code
        self.write('halt', dict(code=code, **details))
        raise Halt(code)

    # Timing ---------------------------------------------------------------
    def wait_until(self, due):
        while True:
            remaining = due - self.clock()
            if remaining <= 0:
                return
            self.sleep(min(remaining / 1e9, 1.0))

    def pace(self):
        """Bounded rate even when late: never burst to catch up."""
        if self.last_send is not None:
            self.wait_until(self.last_send + MIN_COMMAND_INTERVAL_S * 1_000_000_000)
        now = self.clock()
        while self.sends and self.sends[0] <= now - 60_000_000_000:
            self.sends.popleft()
        if len(self.sends) >= MAX_COMMANDS_PER_MINUTE:
            self.wait_until(self.sends[0] + 60_000_000_000)
        self.last_send = self.clock()
        self.sends.append(self.last_send)

    # Exchanges --------------------------------------------------------------
    def health(self, purpose):
        request = dict(version=1, kind='private_health_probe', control_session_id=self.session,
                       request_id=uuid.uuid4().hex)
        raw = json.dumps(request, sort_keys=True, separators=(',', ':'))
        self.write('health_intent', dict(purpose=purpose, request_id=request['request_id'], request_utf8=raw))
        self.pace()
        try:
            reply_raw = self.client.exchange(raw, self.reply_timeout_s)
        except Exception as error:
            self.halt('TRANSPORT_ERROR', request_id=request['request_id'], error=type(error).__name__)
        try:
            reply = check_health_reply(reply_raw, request)
        except ValueError as error:
            self.halt('REPLY_REFUSED', request_id=request['request_id'], error=str(error),
                      reply_utf8=reply_raw if isinstance(reply_raw, str) and len(reply_raw) <= REPLY_LIMIT else None)
        self.write('health_reply', dict(purpose=purpose, request_id=request['request_id'], reply_utf8=reply_raw))
        return reply

    def gate(self, step):
        """Runtime defense in depth: the last acknowledged backend mode."""
        if step['op'] == 'demo' and self.mode != 'teaching':
            self.halt('MODE_GATE', step_index=step['index'], mode=self.mode)
        if step['op'] == 'lock_probe' and self.mode != 'test':
            self.halt('MODE_GATE', step_index=step['index'], mode=self.mode)

    def command(self, step, due, *, fault_id=None, recovery=False):
        op = step['op']
        if not recovery:
            self.gate(step)
        name = 'demo' if op == 'lock_probe' else op
        args = ({'action': step['action'], 'target': step['target']} if op in ('demo', 'lock_probe') else
                {'mode': step['mode']} if op == 'set_mode' else {})
        request = validate(dict(version=1, kind='private_command', control_session_id=self.session,
                                request_id=uuid.uuid4().hex, command=name, args=args))
        raw = json.dumps(request, sort_keys=True, separators=(',', ':'))
        self.pace()
        intent = dict(step_index=step['index'], op=op, request_id=request['request_id'], request_utf8=raw,
                      due_ns=str(due), fault_id=fault_id, recovery=recovery)
        self.write('command_intent', intent)
        sent = self.clock()
        try:
            reply_raw = self.client.exchange(raw, self.demo_timeout_s if name == 'demo' and op == 'demo'
                                             else self.reply_timeout_s)
        except Exception as error:
            # The outcome is unknown. It is never retried or replayed.
            self.halt('TRANSPORT_ERROR', step_index=step['index'], request_id=request['request_id'],
                      error=type(error).__name__)
        received = self.clock()
        try:
            reply = check_reply(reply_raw, request)
        except ValueError as error:
            self.halt('REPLY_REFUSED', step_index=step['index'], request_id=request['request_id'], error=str(error),
                      reply_utf8=reply_raw if isinstance(reply_raw, str) and len(reply_raw) <= REPLY_LIMIT else None)
        outcome = classify(step, reply, allow_missing_demo_content=self.allow_missing_demo_content)
        self.mode = reply['mode']
        digest = sha(reply_raw.encode('utf-8'))
        self.write('command_reply', dict(step_index=step['index'], op=op, request_id=request['request_id'],
                                         reply_utf8=reply_raw, reply_sha256=digest, sent_ns=str(sent),
                                         received_ns=str(received), rtt_ms=(received - sent) / 1e6,
                                         outcome=outcome, fault_id=fault_id, recovery=recovery))
        # Forward before halting so even an unexpected acknowledgement gets a
        # Unity receipt and reaches the analyzer as a measured failure.
        self.publish('command_ack', dict(step_index=step['index'], op=op, request_id=request['request_id'],
                                         reply_sha256=digest, reply_utf8=reply_raw, fault_id=fault_id))
        if outcome == 'unexpected':
            self.halt('UNEXPECTED_OUTCOME', step_index=step['index'], request_id=request['request_id'],
                      reason=reply['reason'])
        if op in ('reset',) or (op == 'set_mode' and step['mode'] == 'test'):
            self.resets[step.get('trial_id')] = request['request_id']
        return reply

    # Faults -----------------------------------------------------------------
    def fault(self, step):
        if self.fault_hook is None:
            self.write('fault_slot_not_injected', dict(step_index=step['index'], fault_type=step['fault_type'],
                                                       reason='NOT_AUTHORIZED'))
            return False
        fault_id = uuid.uuid4().hex
        request = dict(fault_id=fault_id, fault_type=step['fault_type'], station_id=self.schedule['station_id'],
                       step_index=step['index'])
        self.write('fault_intent', dict(request, block_id=step['block_id'], trial_id=step['trial_id']))
        self.publish('fault_marker', dict(step_index=step['index'], fault_id=fault_id, fault_type=step['fault_type'],
                                          block_id=step['block_id'], trial_id=step['trial_id']))
        try:
            result = self.fault_hook(request)
            require(isinstance(result, dict) and len(encoded(result)) <= 4096, 'Fault hook result')
        except Exception as error:
            self.halt('FAULT_HOOK_FAILED', fault_id=fault_id, error=type(error).__name__)
        self.write('fault_hook_returned', dict(fault_id=fault_id, result=result))
        # Never resume automatically: wait for an explicit operator recovery file.
        gate = self.output / 'operator' / f'recovery-{fault_id}.json'
        deadline = self.clock() + int(self.recovery_timeout_s * 1e9)
        while not gate.exists():
            if self.clock() > deadline:
                self.halt('RECOVERY_TIMEOUT', fault_id=fault_id)
            self.sleep(1.0)
        try:
            value, digest = read_operator_file(gate, ('version', 'fault_id', 'control_session_id', 'operator_initiated'))
            require(value['fault_id'] == fault_id and value['operator_initiated'] is True and
                    isinstance(value['control_session_id'], str) and GUID.fullmatch(value['control_session_id']),
                    'Operator recovery file differs')
        except ValueError as error:
            self.halt('RECOVERY_FILE_REFUSED', fault_id=fault_id, error=str(error))
        self.write('operator_recovery', dict(fault_id=fault_id, file_sha256=digest,
                                             control_session_id=value['control_session_id']))
        self.session = value['control_session_id']
        try:
            self.client.reconnect()
        except Exception as error:
            self.halt('TRANSPORT_ERROR', fault_id=fault_id, error=type(error).__name__)
        self.health('recovery')
        recovery_step = dict(step, op='reset')
        reply = self.command(recovery_step, self.clock(), fault_id=fault_id, recovery=True)
        self.write('fault_recovered', dict(fault_id=fault_id, reset_request_id=reply['request_id']))
        return True

    # Run --------------------------------------------------------------------
    def resume_point(self):
        rows = self.journal.rows
        require(rows and rows[0]['kind'] == 'driver_start', 'Resume requires an existing driver journal')
        require(all(r['payload'].get('schedule_sha256', self.schedule_sha256) == self.schedule_sha256
                    for r in rows if r['kind'] == 'driver_start'), 'Resume schedule differs')
        require(not any(r['kind'] == 'driver_end' and r['payload']['completed'] for r in rows),
                'Completed driver journal cannot resume')
        terminal = {r['payload']['request_id'] for r in rows if r['kind'] in ('command_reply', 'halt')
                    and 'request_id' in r['payload']}
        dangling = [r['payload'] for r in rows if r['kind'] == 'command_intent' and r['payload']['request_id'] not in terminal]
        recovered = {r['payload']['fault_id'] for r in rows if r['kind'] == 'fault_recovered'}
        unrecovered = [r['payload']['fault_id'] for r in rows if r['kind'] == 'fault_intent'
                       and r['payload']['fault_id'] not in recovered]
        require(not unrecovered, 'Unrecovered fault in driver journal requires operator review')
        journaled = [r['payload']['feed_seq'] for r in rows if r['kind'] == 'unity_input']
        feed_rows = self.feed.rows
        require(len(feed_rows) - len(journaled) in (0, 1) and journaled == list(range(len(journaled))),
                'Unity input feed and driver journal diverge')
        indices = [r['payload']['step_index'] for r in rows if 'step_index' in r['payload']]
        indices += [r['payload'].get('to_index', -1) for r in rows if r['kind'] == 'skipped']
        last = max(indices, default=-1)
        return dangling, feed_rows[len(journaled):], last

    def run(self):
        steps = self.schedule['steps']
        start_index, base = 0, 0
        try:
            if self.resume:
                dangling, orphan_feed, last = self.resume_point()
                self.write('driver_start', dict(schedule_sha256=self.schedule_sha256, resume=True,
                                                control_session_id=self.session))
                for payload in dangling:
                    # Outcome unknown: retained as evidence, never sent again.
                    self.write('interrupted_exchange', dict(step_index=payload['step_index'],
                                                            request_id=payload['request_id']))
                for row in orphan_feed:
                    self.write('unity_input', dict(feed_seq=row['seq'], feed_sha256=row['sha256'], kind=row['kind'],
                                                   step_index=row['payload']['step_index']))
                start_index = next((s['index'] for s in steps if s['index'] > last and s['op'] == 'block_begin'),
                                   len(steps))
                if start_index > last + 1:
                    self.write('skipped', dict(from_index=last + 1, to_index=start_index - 1, reason='RESUME_BOUNDARY'))
                base = steps[start_index]['t_s'] if start_index < len(steps) else 0
            else:
                self.write('driver_start', dict(schedule_sha256=self.schedule_sha256, resume=False,
                                                station_id=self.schedule['station_id'], seed=self.schedule['seed'],
                                                control_session_id=self.session, generator=GENERATOR,
                                                fault_injection_authorized=self.fault_hook is not None,
                                                allow_missing_demo_content=self.allow_missing_demo_content))
            self.health('start')
            anchor = self.clock()
            index = start_index
            while index < len(steps):
                step = steps[index]
                due = anchor + (step['t_s'] - base) * 1_000_000_000
                self.wait_until(due)
                op = step['op']
                if op in COMMAND_OPS:
                    self.command(step, due)
                elif op == 'fault_slot':
                    if self.fault(step):
                        # Continue only at the next fresh trial of this block or a
                        # later block boundary still in the future. The interrupted
                        # trial is not replayed and no block starts without its mode.
                        now = self.clock()
                        following = next((s['index'] for s in steps[index + 1:]
                                          if anchor + (s['t_s'] - base) * 1_000_000_000 >= now and
                                          (s['op'] == 'block_begin' or
                                           (s['op'] == 'reset' and s['block_id'] == step['block_id']))),
                                         len(steps))
                        if following > index + 1:
                            self.write('skipped', dict(from_index=index + 1, to_index=following - 1,
                                                       reason='FAULT_RECOVERY'))
                        index = following
                        continue
                elif op == 'trial':
                    reset_id = self.resets.get(step['trial_id'])
                    if reset_id is None:
                        self.halt('TRIAL_WITHOUT_RESET', step_index=index)
                    self.publish('trial', dict(step_index=index, block_id=step['block_id'], trial_id=step['trial_id'],
                                               reset_request_id=reset_id))
                elif op == 'block_begin':
                    self.publish('block_begin', dict(step_index=index, block=step['block'], block_id=step['block_id']))
                else:
                    self.publish('dummy_response', dict(step_index=index, block_id=step['block_id'],
                                                        trial_id=step['trial_id'], response_code=step['response_code']))
                index += 1
            self.write('driver_end', dict(completed=True, halted=None))
        except Halt:
            self.write('driver_end', dict(completed=False, halted=self.halted))
        except Exception as error:
            self.halted = 'DRIVER_ERROR'
            self.write('driver_end', dict(completed=False, halted='DRIVER_ERROR', error=type(error).__name__))
        finally:
            self.journal.close()
            self.feed.close()
        return dict(completed=self.halted is None, halted=self.halted,
                    journal_sha256=sha(read_bounded(self.output / 'driver-journal.jsonl', JOURNAL_LIMIT)),
                    feed_sha256=sha(read_bounded(self.output / 'unity-inputs.jsonl', JOURNAL_LIMIT)),
                    g2_signed=False, recommendation='NO_GO')


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def write_new(path, content):
    path = safe_path(path, missing=True)
    with path.open('xb') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('schedule', help='Write a deterministic canonical schedule')
    build.add_argument('--station-id', required=True)
    build.add_argument('--seed', type=int, required=True)
    build.add_argument('--seconds', type=int, required=True)
    build.add_argument('--block-seconds', type=int, default=300)
    build.add_argument('--trial-interval-s', type=int, default=20)
    build.add_argument('--fault-types', default='', help='Comma-separated planned fault slots; empty for none')
    build.add_argument('--output', type=Path, required=True)
    check = commands.add_parser('validate', help='Verify a pinned schedule')
    check.add_argument('schedule', type=Path)
    check.add_argument('--sha256', required=True)
    run = commands.add_parser('run', help='Drive one station over the private command API')
    run.add_argument('--schedule', type=Path, required=True)
    run.add_argument('--sha256', required=True)
    run.add_argument('--control-session-id', required=True)
    target = run.add_mutually_exclusive_group(required=True)
    target.add_argument('--endpoint', help='ws://127.0.0.1:PORT/commands')
    target.add_argument('--unix-socket', type=Path)
    run.add_argument('--output', type=Path, required=True)
    run.add_argument('--resume', action='store_true')
    run.add_argument('--allow-missing-demo-content', action='store_true')
    run.add_argument('--authorize-fault-injection', action='store_true')
    run.add_argument('--fault-hook', choices=('operator-file',))
    run.add_argument('--fault-timeout', type=float, default=900)
    run.add_argument('--recovery-timeout', type=float, default=1800)
    args = parser.parse_args(argv)
    if args.command == 'schedule':
        faults = [x for x in args.fault_types.split(',') if x]
        content = canonical_bytes(build_schedule(station_id=args.station_id, seed=args.seed, seconds=args.seconds,
                                                 block_seconds=args.block_seconds,
                                                 trial_interval_s=args.trial_interval_s, fault_types=faults))
        write_new(args.output, content)
        print(json.dumps(dict(schedule=str(args.output), sha256=sha(content))))
        return 0
    if args.command == 'validate':
        schedule = load_schedule(read_bounded(args.schedule, 16 * 1024 * 1024), args.sha256)
        ops = [step['op'] for step in schedule['steps']]
        # Every scheduled command plus one recovery reset per fault slot occupies
        # one never-evicted dispatcher idempotency entry; size cache_size above it.
        print(json.dumps(dict(valid=True, station_id=schedule['station_id'], steps=len(ops),
                              commands=sum(op in COMMAND_OPS for op in ops) + ops.count('fault_slot'),
                              lock_probes=ops.count('lock_probe'), trials=ops.count('trial'),
                              fault_slots=ops.count('fault_slot'), seconds=schedule['seconds'])))
        return 0
    require(not args.fault_hook or args.authorize_fault_injection, 'Fault hook requires --authorize-fault-injection')
    require(not args.authorize_fault_injection or args.fault_hook, '--authorize-fault-injection requires --fault-hook')
    hook = (OperatorFileFaultHook(args.output / 'operator', timeout_s=args.fault_timeout)
            if args.fault_hook == 'operator-file' else None)
    client = WebSocketCommandClient(endpoint=args.endpoint,
                                    unix_socket=str(args.unix_socket) if args.unix_socket else None)
    driver = SoakDriver(read_bounded(args.schedule, 16 * 1024 * 1024), args.sha256, args.output, client,
                        control_session_id=args.control_session_id, resume=args.resume, fault_hook=hook,
                        authorize_fault_injection=args.authorize_fault_injection,
                        recovery_timeout_s=args.recovery_timeout,
                        allow_missing_demo_content=args.allow_missing_demo_content)
    try:
        result = driver.run()
    finally:
        client.close()
    print(json.dumps(result))
    return 0 if result['completed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
