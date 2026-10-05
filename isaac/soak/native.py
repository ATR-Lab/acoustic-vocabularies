"""Verify exact Unity soak journal bytes; never upgrade observations to authority."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import re
import stat

from .analyze import load_json, require

SHA = re.compile(r"[0-9a-f]{64}\Z")
GUID = re.compile(r"[0-9a-f]{32}\Z")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
MAX_BYTES = 512 * 1024 * 1024
SUFFIX = re.compile(rb',"sha256":"([0-9a-f]{64})"}\n\Z')


def safe_path(value, *, directory=False, missing=False):
    """Reject UNC, symlink/reparse paths, including existing ancestors."""
    p = Path(value).absolute()
    require(not str(value).startswith(('\\\\', '//')), 'UNC paths are unsupported')
    for part in [p, *p.parents]:
        try:
            mode = part.lstat()
        except FileNotFoundError:
            continue
        require(not stat.S_ISLNK(mode.st_mode) and
                not getattr(mode, 'st_file_attributes', 0) & 0x400, 'Linked evidence path')
    if p.exists():
        require(p.is_dir() if directory else p.is_file(), 'Wrong evidence path type')
    else:
        require(missing, 'Missing evidence path')
    return p


def read_bounded(path, limit=MAX_BYTES):
    p = safe_path(path)
    with p.open('rb') as stream:
        data = stream.read(limit + 1)
    require(len(data) <= limit, 'Evidence file exceeds limit')
    return data


def plan_bytes(raw, expected_sha256):
    require(isinstance(expected_sha256, str) and SHA.fullmatch(expected_sha256) and
            hashlib.sha256(raw).hexdigest() == expected_sha256, 'Plan pin mismatch')
    require(0 < len(raw) <= 16384, 'Plan size')
    p = load_json(raw)
    require(isinstance(p, dict) and set(p) == {'version', 'scope', 'participants', 'station_id',
            'build_id', 'scene_sha256', 'snapshot_sha256', 'schedule_sha256', 'source_kind',
            'seconds', 'client_kind', 'substitute_justification'}, 'Plan fields')
    require(type(p['version']) is int and p['version'] == 1 and p['scope'] == 'synthetic_nonstudy'
            and p['participants'] is False and p['source_kind'] == 'live', 'Nonstudy live plan required')
    require(all(isinstance(p[k], str) and ID.fullmatch(p[k]) for k in ('station_id', 'build_id')), 'Plan identity')
    require(all(isinstance(p[k], str) and SHA.fullmatch(p[k]) for k in
                ('scene_sha256', 'snapshot_sha256', 'schedule_sha256')), 'Plan binding')
    require(type(p['seconds']) in (float, int) and math.isfinite(p['seconds']) and
            0 < p['seconds'] <= 36000, 'Plan duration')
    require(p['client_kind'] in ('headset', 'headset_equivalent') and
            isinstance(p['substitute_justification'], str) and len(p['substitute_justification']) <= 1024 and
            (p['client_kind'] == 'headset' or p['substitute_justification'].strip()), 'Client declaration')
    return p


class NativeReader:
    """Incremental reader preserves .NET float spelling in the signed bytes.

    A live incomplete final line is retained until its newline arrives. A final
    truncated line, changed prefix, gap, mixed epoch or false terminal is refused.
    """
    def __init__(self, expected_plan, plan_sha256, *, retain_rows=True):
        self.plan, self.pin = expected_plan, plan_sha256
        self.rows, self.pending = [], b''
        self.previous = '0' * 64
        self.epoch = self.frequency = None
        self.last_time = -1
        self.bytes_seen = 0
        self.retain_rows=retain_rows;self.row_count=0;self.heartbeat_count=0
        self.mirrored=-1;self.last_heartbeat_time=None;self.max_heartbeat_gap=0

    def feed(self, chunk):
        require(isinstance(chunk, bytes), 'Native bytes required')
        self.bytes_seen += len(chunk)
        require(self.bytes_seen <= MAX_BYTES, 'Native journal exceeds limit')
        self.pending += chunk
        fresh = []
        while b'\n' in self.pending:
            line, self.pending = self.pending.split(b'\n', 1)
            line += b'\n'
            require(len(line) <= 65536, 'Native line exceeds limit')
            match = SUFFIX.search(line)
            require(match is not None, 'Native hash must be last exact compact field')
            digest = match[1].decode('ascii')
            require(hashlib.sha256(line[:match.start()] + b'}\n').hexdigest() == digest, 'Native byte hash mismatch')
            r = load_json(line)
            require(isinstance(r, dict) and set(r) == {'version', 'seq', 'clock_epoch', 'clock_domain',
                    'stopwatch_frequency_hz', 't_s', 'kind', 'payload', 'previous_sha256', 'sha256'}, 'Native fields')
            require(type(r['version']) is int and r['version'] == 1 and type(r['seq']) is int and
                    r['seq'] == self.row_count and r['previous_sha256'] == self.previous, 'Native sequence/chain')
            require(isinstance(r['clock_epoch'], str) and GUID.fullmatch(r['clock_epoch']) and
                    r['clock_domain'] == 'unity_stopwatch_seconds' and
                    type(r['stopwatch_frequency_hz']) is int and r['stopwatch_frequency_hz'] > 0, 'Native clock')
            if self.epoch is None:
                self.epoch, self.frequency = r['clock_epoch'], r['stopwatch_frequency_hz']
            require((self.epoch, self.frequency) == (r['clock_epoch'], r['stopwatch_frequency_hz']), 'Mixed native clock')
            require(type(r['t_s']) in (int, float) and math.isfinite(r['t_s']) and
                    r['t_s'] >= max(0, self.last_time), 'Native time regression')
            require(isinstance(r['kind'], str) and ID.fullmatch(r['kind']) and isinstance(r['payload'], dict), 'Native payload')
            require(not self.rows or self.rows[-1]['kind'] != 'session_end', 'Rows after native terminal')
            if not self.rows:
                p = r['payload']
                require(r['kind'] == 'session_start' and p.get('plan') == self.plan and
                        p.get('plan_sha256') == self.pin and
                        p.get('monitor') == 'continuous_receiver_stale_and_freeze_detector' and
                        p.get('arrival_age_basis') == 'latest_accepted_sample_local_arrival' and
                        p.get('mirror_count_basis') == 'distinct_applied_frame_session_sequence' and
                        p.get('render_basis') == 'application_onBeforeRender_not_photons', 'Native runtime binding')
            else:
                require(r['kind'] != 'session_start', 'Duplicate native start')
            if r['kind']=='heartbeat':
                h=r['payload']
                require(h.get('block') in ('teaching','selection','protected','paused') and isinstance(h.get('block_id'),str),'Heartbeat context')
                require(all(type(h.get(k)) in (float,int) and math.isfinite(h[k]) and h[k]>=0 for k in ('state_age_ms','frame_age_ms')),'Heartbeat ages')
                require(type(h.get('mirrored_frames')) is int and h['mirrored_frames']>=self.mirrored,'Mirror counter')
                prior=self.rows[0]['t_s'] if self.last_heartbeat_time is None else self.last_heartbeat_time
                self.max_heartbeat_gap=max(self.max_heartbeat_gap,r['t_s']-prior)
                self.last_heartbeat_time=r['t_s'];self.mirrored=h['mirrored_frames'];self.heartbeat_count+=1
            self.previous, self.last_time = digest, r['t_s']
            if self.retain_rows or len(self.rows)<2:self.rows.append(r)
            else:self.rows[-1]=r
            self.row_count+=1
            fresh.append(r)
        require(len(self.pending) <= 65536, 'Unterminated native line exceeds limit')
        return fresh

    def finish(self):
        require(not self.pending and len(self.rows) >= 2 and self.rows[-1]['kind'] == 'session_end', 'Native capture incomplete')
        end = self.rows[-1]
        p = end['payload']
        require(set(p) == {'completed', 'requested_seconds', 'elapsed_seconds', 'monitor_fault', 'g2_signed'} and
                type(p['completed']) is bool and p['g2_signed'] is False and
                p['requested_seconds'] == self.plan['seconds'] and
                type(p['elapsed_seconds']) in (float, int) and math.isfinite(p['elapsed_seconds']) and
                abs(p['elapsed_seconds'] - (end['t_s'] - self.rows[0]['t_s'])) < 1e-6 and
                (p['monitor_fault'] is None or isinstance(p['monitor_fault'], str)), 'Native terminal fields')
        require(not p['completed'] or p['elapsed_seconds'] >= self.plan['seconds'], 'False native completion')
        last=self.rows[0]['t_s'] if self.last_heartbeat_time is None else self.last_heartbeat_time
        gap=max(self.max_heartbeat_gap,end['t_s']-last)
        return dict(completed=p['completed'], elapsed_seconds=p['elapsed_seconds'], native_rows=self.row_count,
                    native_head_sha256=self.previous, clock_epoch=self.epoch, heartbeat_count=self.heartbeat_count,
                    max_heartbeat_gap_s=gap, monitor_fault=p['monitor_fault'],
                    live_applied_frames=self.mirrored if self.heartbeat_count else 0,
                    receiver_window_complete=bool(p['completed'] and self.heartbeat_count and gap <= 1 and self.mirrored > 0),
                    g2_signed=False, qualification='native_capture_only')


def receiver_events(reader):
    """Losslessly map receiver facts only. This is deliberately not a full soak.

    Reset, trial, lock, recovery and audible-cue facts must be independently
    joined against the private command and durable data journals. Generic host
    observations are retained in native bytes and never treated as proof here.
    """
    require(reader.retain_rows, 'Use streamed receiver events for a streaming reader')
    result = []
    for r in reader.rows:
        row=receiver_event(r,len(result))
        if row is not None:result.append(row)
    return result


def receiver_event(row, sequence):
    kind=row['kind']
    if kind not in ('session_start','session_end','heartbeat','stale_gap','frame_freeze'):return None
    if kind=='session_start':fields={'monitor':row['payload']['monitor']}
    elif kind=='session_end':fields={'completed':row['payload']['completed']}
    else:fields=row['payload'].copy()
    return dict(seq=sequence,t_s=row['t_s'],kind=kind,source_kind='live',clock_domain='unity_monotonic',native_seq=row['seq'],**fields)
