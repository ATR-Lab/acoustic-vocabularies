"""Bounded owner-thread observations of the state actually used for a frame.

Nothing is written until finalization. Stored immutable bytes cannot follow
later simulator/command mutations. A failed observation makes the capture
incomplete; it does not change the ordinary source's guards or control policy.
"""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid

from isaac.commands.protocol import decode, validate
from isaac.publisher.protocol import strict_loads, validate_frame
from isaac.reset.snapshot import canonical_bytes, validate_state
from isaac.soak.native import safe_path, read_bounded

SHA = re.compile(r'[0-9a-f]{64}\Z')
GUID = re.compile(r'[0-9a-f]{32}\Z')
COMMIT = re.compile(r'[0-9a-f]{40}\Z')
TRANSITIONS = frozenset({'reset', 'hold_neutral', 'set_mode', 'pause', 'resume', 'stop'})


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def valid_limits(max_records, max_serialized_bytes, max_seconds):
    if (type(max_records) is not int or not 1 <= max_records <= 4096
            or type(max_serialized_bytes) is not int or not 1024 <= max_serialized_bytes <= 256*1024*1024
            or type(max_seconds) not in (int, float) or not math.isfinite(max_seconds)
            or not 1 <= max_seconds <= 900):
        raise ValueError('Explicit bounded observation limits required')


def validate_options(value, service_seconds):
    if not isinstance(value, dict) or set(value) != {
            'plan_sha256','source_commit','max_records','max_serialized_bytes','max_seconds'}:
        raise ValueError('Exact observation options required; no task content input')
    if (not isinstance(value['plan_sha256'],str) or not SHA.fullmatch(value['plan_sha256'])
            or not isinstance(value['source_commit'],str) or not COMMIT.fullmatch(value['source_commit'])):
        raise ValueError('Independent plan and source pins required')
    valid_limits(value['max_records'],value['max_serialized_bytes'],value['max_seconds'])
    if value['max_seconds'] > service_seconds:
        raise ValueError('Observation duration must fit the source lease')


class ObservationJournal:
    """One explicit opt-in, bound to a public registry and control session.

max_records bounds the combined reset-reply and observation rows. Reaching a
capacity is incomplete, even if no later callback attempts to add another row.
The header also counts toward serialized-byte capacity. No ring overwrites.
"""
    def __init__(self, output, *, plan_sha256, source_commit, registry, public_session_id,
                 control_session_id, allowed_client, commands_path, max_records,
                 max_serialized_bytes, max_seconds, clock_ns=time.monotonic_ns):
        valid_limits(max_records, max_serialized_bytes, max_seconds)
        if (not SHA.fullmatch(plan_sha256) or not COMMIT.fullmatch(source_commit)
                or not GUID.fullmatch(public_session_id) or not GUID.fullmatch(control_session_id)
                or not isinstance(allowed_client, str) or not allowed_client):
            raise ValueError('Pinned observation identities required')
        self.output = safe_path(output, directory=True, missing=True)
        if self.output.exists():
            raise FileExistsError('Preserve prior capture output')
        self.commands_path = safe_path(commands_path, missing=True)
        self.registry, self.allowed_client = registry, allowed_client
        self.owner = threading.get_ident()
        self.clock_ns, self.start_ns, self.previous_ns = clock_ns, None, None
        self.max_records, self.max_bytes = max_records, max_serialized_bytes
        self.max_ns = int(max_seconds*1e9)
        self.fault = None
        self.closed = False
        self.lineage = None
        self.observations, self.replies = [], []
        self.last_seq = self.last_step = None
        self.reset_ids = set()
        self.result = None
        self.header = dict(version=1, kind='view_state_observation_header',
            plan_sha256=plan_sha256, source_commit=source_commit, station_id=registry.station_id,
            control_session_id=control_session_id, public_session_id=public_session_id,
            scene_sha256=registry.scene_sha256, snapshot_sha256=registry.reset_snapshot_sha256,
            source_clock_id=uuid.uuid4().hex, source_clock_kind='host_monotonic_ns',
            limits=dict(max_records=max_records, max_serialized_bytes=max_serialized_bytes,
                        max_seconds=max_seconds))
        self.header_bytes = canonical_bytes(self.header)
        self.bytes_retained = len(self.header_bytes)
        if self.bytes_retained >= self.max_bytes:
            raise ValueError('Header exceeds observation capacity')

    def fail(self, code):
        if self.fault is None:
            self.fault = code
        self.lineage = None

    def start(self):
        if threading.get_ident() != self.owner or self.start_ns is not None or self.closed:
            self.fail('OBS_START_INVALID')
            return
        self.start_ns = self.clock_ns()

    def _active(self):
        if self.closed or self.fault or self.start_ns is None:
            return False
        if threading.get_ident() != self.owner:
            self.fail('OBS_OWNER_CHANGED')
            return False
        now = self.clock_ns()
        if now < self.start_ns or self.previous_ns is not None and now < self.previous_ns:
            self.fail('OBS_CLOCK_REGRESSED')
            return False
        if now-self.start_ns >= self.max_ns:
            self.fail('OBS_DURATION_LIMIT')
            return False
        self.previous_ns = now
        return True

    def _append(self, collection, row):
        raw = canonical_bytes(row)
        count = len(self.replies)+len(self.observations)
        if count+1 > self.max_records or self.bytes_retained+len(raw) > self.max_bytes:
            self.fail('OBS_CAPACITY_EXCEEDED')
            return False
        collection.append(raw)
        self.bytes_retained += len(raw)
        if count+1 == self.max_records or self.bytes_retained == self.max_bytes:
            self.fail('OBS_CAPACITY_REACHED')
        return self.fault is None

    def command_sink(self, durable_sink):
        def write(event):
            try:
                locator = durable_sink(event)  # No lineage before durable success.
            except Exception:
                self.fail('OBS_COMMAND_LOG_FAILED')
                raise
            self.command_written(event)
            return locator  # Lets the dispatcher evict idempotency entries to the durable log.
        return write

    def command_written(self, event):
        """Observe the actual durable event, without storing its hidden arguments."""
        try:
            command = event.get('command')
            if command not in TRANSITIONS:
                return
            self.lineage = None  # Failed/replayed transitions cannot preserve old proof.
            if not self._active() or command != 'reset':
                return
            reply = event['reply']
            if not (reply.get('accepted') is True and reply.get('reset_ok') is True
                    and reply.get('duplicate') is False and reply.get('reason') == 'RESET_COMPLETE'
                    and reply.get('mode') == 'test' and event.get('mode') == 'test'):
                return
            request = validate(decode(event['raw_command']))
            if (request['command'] != 'reset' or request['args'] != {}
                    or request['control_session_id'] != self.header['control_session_id']
                    or event['client'] != self.allowed_client
                    or event['station_id'] != self.header['station_id']
                    or reply['request_id'] != request['request_id']
                    or reply['health']['control_session_id'] != self.header['control_session_id']):
                raise ValueError('Reset lineage mismatch')
            reply_hash = digest(canonical_bytes(reply))
            if request['request_id'] in self.reset_ids:
                raise ValueError('Reset request reused')
            row = dict(version=1, kind='view_reset_reply', request_id=request['request_id'],
                       reply_canonical_sha256=reply_hash, reply=reply)
            if self._append(self.replies, row):
                self.reset_ids.add(request['request_id'])
                self.lineage = (request['request_id'], reply_hash)
        except Exception:
            self.fail('OBS_RESET_INVALID')

    def after_publish(self, frame, payload, state, observed_host_ns, *, protected, neutral_valid):
        """Capture only the complete actual sample which constructed this frame.

observed_host_ns is taken immediately after the owner's sample() returned.
This method is called after successful transport queue insertion; that is not
proof of delivery or rendering. The native journal must match exact identities.
"""
        try:
            if not self._active() or self.lineage is None:
                return
            if protected is not True or neutral_valid is not True:
                raise ValueError('Protected neutral observation required')
            validate_state(state)
            validate_frame(frame, self.registry)
            if not isinstance(payload, str) or strict_loads(payload) != frame:
                raise ValueError('Exact wire frame mismatch')
            if (frame['source_kind'] != 'live' or frame['session_id'] != self.header['public_session_id']
                    or type(observed_host_ns) is not int or observed_host_ns < self.start_ns
                    or observed_host_ns > int(frame['host_monotonic_ns'])
                    or int(frame['host_monotonic_ns']) > self.previous_ns):
                raise ValueError('Observation clock or source mismatch')
            robot = state['robot']
            if robot['joint_names'] != frame['joint_names'] or robot['joint_positions_rad'] != frame['joint_positions']:
                raise ValueError('Joint projection mismatch')
            if set(state['objects']) != {obj['id'] for obj in frame['objects']}:
                raise ValueError('Object registry mismatch')
            for obj in frame['objects']:
                actual = state['objects'][obj['id']]
                if any(actual[key] != obj[key] for key in ('position_m','rotation_xyzw','visible','enabled','state')):
                    raise ValueError('Object projection mismatch')
            if self.last_seq is not None:
                if frame['seq'] <= self.last_seq or frame['sim_step'] <= self.last_step:
                    raise ValueError('Publication does not progress')
            self._append(self.observations, dict(version=1, kind='view_state_observation',
                observation_id=uuid.uuid4().hex, observed_host_ns=str(observed_host_ns),
                reset_request_id=self.lineage[0], reset_reply_canonical_sha256=self.lineage[1],
                public_session_id=frame['session_id'], sequence=frame['seq'], sim_step=frame['sim_step'],
                frame_sha256=digest(payload.encode('utf-8')), frame_utf8=payload, frame=frame, state=state))
            self.last_seq, self.last_step = frame['seq'], frame['sim_step']
        except Exception:
            self.fail('OBS_STATE_FRAME_INVALID')

    def close(self):
        if self.closed:
            return self.result
        # A stalled final interval may have no later publication callback.
        # Enforce the same owner/clock/duration bound at finalization too.
        self._active()
        self.closed = True
        if not self.observations or not self.replies:
            self.fail('OBS_NO_PAIRED_ROWS')
        files = {'observations': None, 'reset_replies': None, 'commands': None}
        try:
            safe_path(self.output, directory=True, missing=True)
            self.output.mkdir(parents=True, mode=0o700, exist_ok=False)
        except Exception:
            self.fail('OBS_OUTPUT_FAILED')
            # Never write through an output path we did not exclusively create.
            self.result = self._manifest(files)
            return self.result
        for key, name, chunks in [
                ('observations','source-observations.jsonl',[self.header_bytes, *self.observations]),
                ('reset_replies','reset-replies.jsonl',self.replies)]:
            path = self.output/name
            try:
                with path.open('xb') as handle:
                    for raw in chunks: handle.write(raw)
                    handle.flush(); os.fsync(handle.fileno())
                raw = path.read_bytes()
                files[key] = dict(path=name, sha256=digest(raw), bytes=len(raw))
            except Exception:
                self.fail('OBS_WRITE_FAILED')
        try:
            raw = read_bounded(self.commands_path,256*1024*1024)
            if len(raw.splitlines()) > 4096:
                raise ValueError('Command journal row bound exceeded')
            with (self.output/'commands.jsonl').open('xb') as handle:
                handle.write(raw);handle.flush();os.fsync(handle.fileno())
            files['commands'] = dict(path='commands.jsonl',sha256=digest(raw),bytes=len(raw))
        except Exception:
            self.fail('OBS_COMMANDS_UNAVAILABLE')
        self.result = self._manifest(files)
        try:
            temporary=self.output/'.manifest.json.tmp'
            with temporary.open('xb') as handle:
                handle.write(canonical_bytes(self.result)); handle.flush(); os.fsync(handle.fileno())
            # Publish only after the manifest's own bytes are durable; failure
            # must not leave a successfully named complete=true manifest.
            os.link(temporary,self.output/'manifest.json')
        except Exception:
            self.fail('OBS_MANIFEST_FAILED')
            self.result.update(complete=False,fault=self.fault)
        else:
            try:temporary.unlink()
            except OSError:pass  # Extra identical temporary name is not authority.
        return self.result

    def _manifest(self, files):
        return dict(version=1,kind='view_state_observation_manifest',
            plan_sha256=self.header['plan_sha256'],source_commit=self.header['source_commit'],
            complete=self.fault is None,fault=self.fault,observation_count=len(self.observations),
            reset_reply_count=len(self.replies),files=files)
