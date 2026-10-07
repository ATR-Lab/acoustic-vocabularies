"""Plain public-v2 NDJSON with separate private, durable capture metadata.

Recordings follow a fixed sim-step schedule: each of the 300 samples is taken
after exactly ``PHYSICS_STEPS_PER_SAMPLE`` physics steps, so every recorded
demonstration spans the same 600 steps (10 s at 60 Hz) by construction. Host
monotonic stamps are retained as capture provenance only. Participant-facing
playback is paced on the playback host's monotonic clock at the fixed sample
period (sample index / 30 Hz); it never uses ``sim_time`` as its clock.
"""
import hashlib
import json
import math
import os
from pathlib import Path

from isaac.publisher.protocol import validate_frame, strict_loads, encode
from .runtime import (SAMPLE_COUNT, SAMPLE_HZ, NOMINAL_DURATION_SECONDS,
                      PHYSICS_STEPS_PER_SAMPLE, PHYSICS_DT_SECONDS, TOTAL_PHYSICS_STEPS)


# Float simulator clocks accumulate rounding; the integer step counter is the
# authority and sim_time is only an independent cross-check of each sample.
SIM_TIME_TOLERANCE_SECONDS = 1e-5
SCHEDULE = dict(kind='fixed_sim_step', sample_count=SAMPLE_COUNT, sample_hz=SAMPLE_HZ,
                physics_steps_per_sample=PHYSICS_STEPS_PER_SAMPLE,
                physics_dt_seconds=PHYSICS_DT_SECONDS, total_physics_steps=TOTAL_PHYSICS_STEPS,
                recorded_duration_seconds=NOMINAL_DURATION_SECONDS)
TIMING_RULE = ('fixed sim-step schedule: 300 samples x 2 physics steps at 1/60 s = 600 steps (10 s) '
               'for every demo; host stamps are capture provenance; playback is paced on the host '
               'monotonic clock at sample index/30 Hz and never on sim_time; provisional engineering rule')


def write_json(path, value):
    path = Path(path)
    data = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False)+'\n').encode()
    temporary = path.with_name(path.name+'.writing')
    with temporary.open('xb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _progress(old, frame):
    if (frame['session_id'] != old['session_id'] or frame['seq'] != old['seq']+1
            or int(frame['host_monotonic_ns']) <= int(old['host_monotonic_ns'])
            or frame['sim_time'] <= old['sim_time'] or frame['sim_step'] <= old['sim_step']):
        raise ValueError('Trajectory identity, host clock or simulation failed to progress')


def _schedule(index, frame, old):
    """Sample ``index`` (0-based) must follow exactly 2*(index+1) demo physics steps."""
    if frame['sim_step'] != (index+1)*PHYSICS_STEPS_PER_SAMPLE:
        raise ValueError('Fixed sim-step schedule violated: sample %d has sim_step %r, expected %d'
                         % (index, frame['sim_step'], (index+1)*PHYSICS_STEPS_PER_SAMPLE))
    if old is not None:
        delta = frame['sim_time']-old['sim_time']
        if abs(delta-PHYSICS_STEPS_PER_SAMPLE*PHYSICS_DT_SECONDS) > SIM_TIME_TOLERANCE_SECONDS:
            raise ValueError('Fixed sim-step schedule violated: sample %d advanced %.9f s of simulation time'
                             % (index, delta))


class TrajectoryWriter:
    """Refuses any frame that leaves the fixed sim-step schedule."""

    def __init__(self, path, registry):
        self.path, self.registry = Path(path), registry
        self.handle = self.path.open('xb')
        self.first = self.previous = None
        self.count = 0
        self.intervals_ms = []

    def append(self, frame):
        validate_frame(frame, self.registry)
        if self.previous:
            _progress(self.previous, frame)
        if self.count >= SAMPLE_COUNT:
            raise ValueError('Fixed sim-step schedule violated: more than %d samples' % SAMPLE_COUNT)
        _schedule(self.count, frame, self.previous)
        if self.previous:
            self.intervals_ms.append((int(frame['host_monotonic_ns'])-int(self.previous['host_monotonic_ns']))/1e6)
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
        steps = 0 if not self.previous else self.previous['sim_step']
        schedule_ok = bool(completed and self.count == SAMPLE_COUNT and steps == TOTAL_PHYSICS_STEPS)
        return dict(file=self.path.name, sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(),
                    frame_count=self.count, nominal_sample_hz=SAMPLE_HZ,
                    nominal_duration_seconds=NOMINAL_DURATION_SECONDS,
                    schedule=dict(SCHEDULE), recorded_physics_steps=steps,
                    recorded_duration_seconds=steps*PHYSICS_DT_SECONDS,
                    capture_complete=bool(completed), schedule_ok=schedule_ok,
                    capture_host_seconds=span, capture_interval_ms=interval,
                    actual_host_timestamps_preserved=True, time_compressed=False,
                    playback_clock='host_monotonic_fixed_sample_period',
                    timing_rule=TIMING_RULE)


def record_fixed_schedule(writer, encoder, *, physics_step, advance, finished, sample):
    """Capture exactly SAMPLE_COUNT samples, each after PHYSICS_STEPS_PER_SAMPLE steps.

    Capture is deliberately unpaced: it never sleeps against a host deadline,
    so host load cannot change the recorded duration. ``sample()`` returns
    ``(joint_positions, public_objects, sim_time)``; the demo-relative step count
    is the integer the writer checks. Returns the number of steps taken.
    """
    steps = 0
    for index in range(SAMPLE_COUNT):
        for _ in range(PHYSICS_STEPS_PER_SAMPLE):
            physics_step()
            steps += 1
        advance()
        if finished():
            raise RuntimeError('Demo ended before its fixed sample count at sample %d' % index)
        positions, objects, sim_time = sample()
        writer.append(encoder.build(positions, objects, float(sim_time), steps))
    return steps


def validate_suite(records, *, expected=40):
    """Refuse a suite unless every recording has the identical fixed duration."""
    if len(records) != expected:
        raise ValueError('Fixed-duration suite requires %d recordings, found %d' % (expected, len(records)))
    failures = []
    for number, record in enumerate(records):
        if record.get('schedule') != SCHEDULE:
            failures.append('%03d:schedule' % number)
        if not record.get('capture_complete') or not record.get('schedule_ok'):
            failures.append('%03d:incomplete' % number)
        if record.get('frame_count') != SAMPLE_COUNT:
            failures.append('%03d:frame_count=%r' % (number, record.get('frame_count')))
        if record.get('recorded_physics_steps') != TOTAL_PHYSICS_STEPS:
            failures.append('%03d:physics_steps=%r' % (number, record.get('recorded_physics_steps')))
    durations = {record.get('recorded_physics_steps') for record in records}
    if failures or len(durations) != 1:
        raise ValueError('Recorded demonstrations do not share the fixed sim-step duration: '
                         +', '.join(failures or ['durations=%r' % sorted(durations, key=str)]))
    return dict(SCHEDULE, recordings=len(records), identical_physics_steps=TOTAL_PHYSICS_STEPS)


def playback_offset_seconds(index):
    """Host-clock display offset of sample ``index``; independent of any frame clock."""
    if type(index) is not int or not 0 <= index < SAMPLE_COUNT:
        raise ValueError('Sample index outside the fixed schedule')
    return index/SAMPLE_HZ


def playback_index(elapsed_host_seconds):
    """Sample to display after ``elapsed_host_seconds`` of host monotonic time.

    Returns None once the nominal boundary is reached; the last sample is held
    from 299/30 s until 10 s. Never consults ``sim_time`` or capture stamps.
    """
    if not math.isfinite(elapsed_host_seconds) or elapsed_host_seconds < 0:
        raise ValueError('Elapsed host time must be finite and nonnegative')
    if elapsed_host_seconds >= NOMINAL_DURATION_SECONDS:
        return None
    return min(SAMPLE_COUNT-1, int(math.floor(elapsed_host_seconds*SAMPLE_HZ+1e-9)))


def read_trajectory(path, expected_hash, registry):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError('Trajectory SHA-256 mismatch')
    frames = [validate_frame(strict_loads(line), registry) for line in raw.decode('utf-8').splitlines()]
    if not frames:
        raise ValueError('Empty trajectory')
    for old, frame in zip(frames, frames[1:]):
        try:
            _progress(old, frame)
        except ValueError:
            raise ValueError('Nonprogressing trajectory') from None
    if len(frames) != SAMPLE_COUNT:
        raise ValueError('Fixed sim-step schedule violated: %d samples' % len(frames))
    for index, frame in enumerate(frames):
        _schedule(index, frame, frames[index-1] if index else None)
    return frames
