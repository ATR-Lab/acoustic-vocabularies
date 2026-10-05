"""Plain public-v2 NDJSON with separate private, durable capture metadata."""
import hashlib
import json
import os
from pathlib import Path

from isaac.publisher.protocol import validate_frame, strict_loads, encode
from .runtime import SAMPLE_COUNT, SAMPLE_HZ, NOMINAL_DURATION_SECONDS


def write_json(path, value):
    path = Path(path)
    data = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False)+'\n').encode()
    temporary = path.with_name(path.name+'.writing')
    with temporary.open('xb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


class TrajectoryWriter:
    def __init__(self, path, registry):
        self.path, self.registry = Path(path), registry
        self.handle = self.path.open('xb')
        self.first = self.previous = None
        self.count = 0
        self.intervals_ms = []

    def append(self, frame):
        validate_frame(frame, self.registry)
        if self.previous:
            old = self.previous
            if (frame['session_id'] != old['session_id'] or frame['seq'] != old['seq']+1
                    or int(frame['host_monotonic_ns']) <= int(old['host_monotonic_ns'])
                    or frame['sim_time'] <= old['sim_time'] or frame['sim_step'] <= old['sim_step']):
                raise ValueError('Trajectory identity, host clock or simulation failed to progress')
            self.intervals_ms.append((int(frame['host_monotonic_ns'])-int(old['host_monotonic_ns']))/1e6)
        else:
            self.first = frame
        self.handle.write((encode(frame)+'\n').encode())
        self.previous = frame
        self.count += 1

    def close(self, *, completed):
        if not self.handle.closed:
            self.handle.flush()
            os.fsync(self.handle.fileno())
            self.handle.close()
        span = None if not self.first else (int(self.previous['host_monotonic_ns'])-int(self.first['host_monotonic_ns']))/1e9
        ordered = sorted(self.intervals_ms)
        interval = None if not ordered else dict(min=min(ordered), max=max(ordered),
            p95=ordered[max(0, (95*len(ordered)+99)//100-1)], mean=sum(ordered)/len(ordered))
        timing_ok = bool(completed and self.count == SAMPLE_COUNT and span <= NOMINAL_DURATION_SECONDS
                         and ordered and max(ordered) <= 250.)
        return dict(file=self.path.name, sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(),
                    frame_count=self.count, nominal_sample_hz=SAMPLE_HZ,
                    nominal_duration_seconds=NOMINAL_DURATION_SECONDS,
                    measured_first_to_last_host_seconds=span, interval_ms=interval,
                    capture_complete=bool(completed), timing_ok=timing_ok,
                    actual_host_timestamps_preserved=True, time_compressed=False,
                    timing_rule='300 samples, first-to-last span <= 10 s, no gap > 250 ms; provisional engineering screen')


def read_trajectory(path, expected_hash, registry):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError('Trajectory SHA-256 mismatch')
    frames = [validate_frame(strict_loads(line), registry) for line in raw.decode('utf-8').splitlines()]
    if not frames:
        raise ValueError('Empty trajectory')
    for old, frame in zip(frames, frames[1:]):
        if (frame['session_id'] != old['session_id'] or frame['seq'] != old['seq']+1
                or int(frame['host_monotonic_ns']) <= int(old['host_monotonic_ns'])
                or frame['sim_time'] <= old['sim_time'] or frame['sim_step'] <= old['sim_step']):
            raise ValueError('Nonprogressing trajectory')
    return frames
