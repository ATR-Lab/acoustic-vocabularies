"""Durable, hash-chained start/stop/restart identity records for one station.

One append-only JSONL journal per station output directory. Every record is
fsynced before the service proceeds. A restart reads and verifies the whole
chain first: a different station, a changed configuration hash, or a broken or
partially written chain refuses the start instead of silently starting over.
A run without a ``stopped`` record (SIGKILL, power loss, hung shutdown) is
reported on the next start as ``previous_stop_recorded=false``.

The chain detects edits to any record that has a successor and any partial
final line. Removing complete trailing records is not detectable from the file
alone: copy the journal SHA-256 into the private evidence manifest after each
stop so a later truncation is visible.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

KINDS = {'start_refused', 'starting', 'component_started', 'inventory', 'started', 'ready',
         'gateway_refused', 'stop_requested', 'component_stopped', 'stopped'}
ROW_FIELDS = {'version', 'kind', 'station_id', 'config_sha256', 'run_id', 'sequence', 'previous_sha256',
              'utc', 'host_monotonic_ns', 'payload'}
ZERO = '0'*64
HASH = re.compile(r'[0-9a-f]{64}\Z')
RUN = re.compile(r'[0-9a-f]{32}\Z')


def canonical(row):
    return (json.dumps(row, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode()


def _strict(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate lifecycle field')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs)


def read_history(path):
    """Verify the complete chain; any truncation, edit or reordering refuses."""
    path = Path(path)
    if not path.exists():
        return []
    raw = path.read_bytes()
    if raw and not raw.endswith(b'\n'):
        raise ValueError('Lifecycle journal ends with a partial record; inspect before restarting')
    previous, rows = ZERO, []
    for index, line in enumerate(raw.splitlines(keepends=True)):
        row = _strict(line)
        if (not isinstance(row, dict) or set(row) != ROW_FIELDS or row['kind'] not in KINDS
                or row['sequence'] != index or row['previous_sha256'] != previous or canonical(row) != line):
            raise ValueError('Lifecycle journal chain is broken at record '+str(index))
        previous = hashlib.sha256(line).hexdigest()
        rows.append(row)
    return rows


def restart_identity(rows, station_id, config_sha256):
    """Identity of this start relative to every earlier run in the journal."""
    if any(row['station_id'] != station_id for row in rows):
        raise ValueError('Lifecycle journal belongs to another station')
    configs = {row['config_sha256'] for row in rows if row['kind'] != 'start_refused'}
    if configs - {config_sha256}:
        raise ValueError('Station configuration changed since a previous run; use a new output directory '
                         'for a new apparatus version')
    runs = []
    for row in rows:
        if row['kind'] == 'starting' and row['run_id'] not in runs:
            runs.append(row['run_id'])
    last = runs[-1] if runs else None
    stopped = [row for row in rows if row['kind'] == 'stopped' and row['run_id'] == last]
    return dict(start_index=len(runs), restart=bool(runs), previous_run_id=last,
                previous_stop_recorded=bool(stopped) if last else None,
                previous_exit_code=stopped[-1]['payload'].get('exit_code') if stopped else None)


class LifecycleJournal:
    def __init__(self, path, *, station_id, config_sha256, run_id=None, clock_ns=time.monotonic_ns,
                 wall_ns=time.time_ns, check_identity=True):
        if not isinstance(station_id, str) or not station_id or not HASH.fullmatch(config_sha256 or ''):
            raise ValueError('Explicit station and configuration hash required')
        self.path = Path(path)
        if not self.path.parent.is_dir() or self.path.is_symlink():
            raise ValueError('Lifecycle journal needs an existing private output directory')
        rows = read_history(self.path)
        if check_identity:
            self.identity = restart_identity(rows, station_id, config_sha256)
        else:
            if any(row['station_id'] != station_id for row in rows):
                raise ValueError('Lifecycle journal belongs to another station')
            self.identity = None
        self.station_id, self.config_sha256 = station_id, config_sha256
        self.run_id = run_id or uuid.uuid4().hex
        if not RUN.fullmatch(self.run_id):
            raise ValueError('Opaque 32-hex run id required')
        self.clock_ns, self.wall_ns = clock_ns, wall_ns
        self.sequence = len(rows)
        self.previous = hashlib.sha256(canonical(rows[-1])).hexdigest() if rows else ZERO
        created = not self.path.exists()
        self.stream = self.path.open('ab')
        if created and hasattr(os, 'O_DIRECTORY'):
            descriptor = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
        self.lock = threading.Lock()

    def record(self, kind, **payload):
        if kind not in KINDS:
            raise ValueError('Unknown lifecycle record kind')
        with self.lock:
            if self.stream.closed:
                raise RuntimeError('Lifecycle journal closed')
            wall = self.wall_ns()
            row = dict(version=1, kind=kind, station_id=self.station_id, config_sha256=self.config_sha256,
                       run_id=self.run_id, sequence=self.sequence, previous_sha256=self.previous,
                       utc=datetime.fromtimestamp(wall/1e9, timezone.utc).isoformat(timespec='milliseconds'),
                       host_monotonic_ns=str(self.clock_ns()), payload=payload)
            line = canonical(row)
            self.stream.write(line)
            self.stream.flush()
            os.fsync(self.stream.fileno())
            self.sequence += 1
            self.previous = hashlib.sha256(line).hexdigest()
            return row

    def close(self):
        with self.lock:
            if not self.stream.closed:
                self.stream.close()


def record_refusal(path, *, station_id, config_sha256, reason):
    """Append a refusal when the journal is intact and belongs to this station.

    Returns False (and writes nothing) for a broken or foreign journal, which
    must be inspected by an operator instead of being appended to.
    """
    try:
        journal = LifecycleJournal(path, station_id=station_id, config_sha256=config_sha256,
                                   check_identity=False)
    except (ValueError, OSError):
        return False
    try:
        journal.record('start_refused', reason=str(reason)[:2000])
    finally:
        journal.close()
    return True
