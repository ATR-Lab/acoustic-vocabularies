"""Continuous, independent public-state trace for the station cross-talk audit.

Endpoint snapshots cannot see an excursion that returns before a command reply.
Here every station has its own sampler thread and its own public-state adapter
(a separate connection from the command client). Each fresh frame is validated
against that station's registry and compared with its post-reset baseline using
an explicitly declared physical tolerance. Coverage is measured, never assumed:
an interval between consecutive fresh samples longer than ``max_gap_s`` is
uncovered time. An excursion shorter than ``max_gap_s`` can still be missed;
the report states that detection limit instead of hiding it.

Sample times are the auditor's monotonic receipt clock, the same clock used to
timestamp command windows. ``settle_s`` must cover publication latency plus at
least one publisher period; the operator declares it with a rationale.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import threading
import time

from isaac.publisher.protocol import validate_frame

POLICY_FIELDS = {'version', 'sample_hz', 'max_gap_s', 'min_coverage', 'settle_s', 'joint_tolerance_rad',
                 'position_tolerance_m', 'rotation_tolerance_rad', 'visual_scalar_tolerance', 'rationale'}
# Upper bounds stop a policy from declaring an obviously meaningless tolerance.
LIMITS = dict(joint_tolerance_rad=0.2, position_tolerance_m=0.05, rotation_tolerance_rad=0.2,
              visual_scalar_tolerance=0.1)
SCALAR_VISUAL = {'arrow_angle_rad', 'lid_open_fraction'}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_policy(policy):
    """Exact declared policy; there are no implicit defaults."""
    if not isinstance(policy, dict) or set(policy) != POLICY_FIELDS:
        raise ValueError('Exact continuous-trace policy fields required')
    if type(policy['version']) is not int or policy['version'] != 1:
        raise ValueError('Unsupported continuous-trace policy')
    if type(policy['sample_hz']) is not int or not 1 <= policy['sample_hz'] <= 1000:
        raise ValueError('Declared sample rate must be an integer 1..1000 Hz')
    gap = policy['max_gap_s']
    if not _number(gap) or not 1/policy['sample_hz'] <= gap <= 2.0:
        raise ValueError('Maximum sample gap must be at least one sample period and at most 2 s')
    if not _number(policy['min_coverage']) or not 0 < policy['min_coverage'] <= 1:
        raise ValueError('Minimum coverage must be in (0, 1]')
    if not _number(policy['settle_s']) or not 0 <= policy['settle_s'] <= 10:
        raise ValueError('Settle time must be 0..10 s')
    for key, bound in LIMITS.items():
        if not _number(policy[key]) or not 0 <= policy[key] <= bound:
            raise ValueError('Declared tolerance out of range: '+key)
    if not isinstance(policy['rationale'], str) or not policy['rationale'].strip() or len(policy['rationale']) > 2000:
        raise ValueError('A written rationale for the declared tolerance is required')
    return deepcopy(policy)


def policy_sha256(policy):
    raw = json.dumps(validate_policy(policy), sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256((raw+'\n').encode()).hexdigest()


def quaternion_angle(a, b):
    dot = min(1.0, abs(sum(x*y for x, y in zip(a, b))))
    return 2*math.acos(dot)


def deviation(baseline, frame):
    """Maximum deviation of one validated frame from the same station's baseline."""
    if [o['id'] for o in baseline['objects']] != [o['id'] for o in frame['objects']]:
        raise ValueError('Object inventory differs from baseline')
    joint = max(abs(a-b) for a, b in zip(baseline['joint_positions'], frame['joint_positions']))
    position = rotation = scalar = 0.0
    discrete = 0
    for before, after in zip(baseline['objects'], frame['objects']):
        position = max(position, math.dist(before['position_m'], after['position_m']))
        rotation = max(rotation, quaternion_angle(before['rotation_xyzw'], after['rotation_xyzw']))
        discrete += (before['visible'] != after['visible']) + (before['enabled'] != after['enabled'])
        if set(before['state']) != set(after['state']):
            discrete += 1
            continue
        for key, value in before['state'].items():
            if key in SCALAR_VISUAL:
                scalar = max(scalar, abs(value-after['state'][key]))
            elif value != after['state'][key] or type(value) is not type(after['state'][key]):
                discrete += 1
    return dict(joint_rad=joint, position_m=position, rotation_rad=rotation,
                visual_scalar=scalar, discrete_mismatches=discrete)


def within_tolerance(dev, policy):
    return (dev['joint_rad'] <= policy['joint_tolerance_rad']
            and dev['position_m'] <= policy['position_tolerance_m']
            and dev['rotation_rad'] <= policy['rotation_tolerance_rad']
            and dev['visual_scalar'] <= policy['visual_scalar_tolerance']
            and dev['discrete_mismatches'] == 0)


def covered_ns(times_ns, start_ns, end_ns, max_gap_ns):
    """Nanoseconds of [start, end] between consecutive fresh samples <= max_gap apart.

    ``times_ns`` must include the bracketing samples just outside the window;
    a window part before the first or after the last sample is uncovered.
    """
    if type(start_ns) is not int or type(end_ns) is not int or end_ns < start_ns:
        raise ValueError('Ordered integer window required')
    times = sorted(times_ns)
    if end_ns == start_ns:
        if start_ns in times:
            return 0, True
        return 0, any(a <= start_ns <= b and b-a <= max_gap_ns for a, b in zip(times, times[1:]))
    covered = 0
    for a, b in zip(times, times[1:]):
        if b-a > max_gap_ns:
            continue
        lo, hi = max(a, start_ns), min(b, end_ns)
        if hi > lo:
            covered += hi-lo
    return covered, covered == end_ns-start_ns


def window_coverage(times_ns, start_ns, end_ns, max_gap_ns):
    covered, complete = covered_ns(times_ns, start_ns, end_ns, max_gap_ns)
    if end_ns == start_ns:
        return 1.0 if complete else 0.0
    return covered/(end_ns-start_ns)


def evaluate_window(samples, *, target, start_ns, end_ns, policy):
    """Coverage and tolerance for every non-target station over one command window.

    ``samples`` maps station -> list of (receipt_ns, deviation) fresh samples.
    """
    policy = validate_policy(policy)
    gap = int(round(policy['max_gap_s']*1e9))
    rows = []
    for station in sorted(samples):
        if station == target:
            continue
        series = sorted(samples[station], key=lambda item: item[0])
        times = [t for t, _ in series]
        before = [t for t in times if t <= start_ns]
        after = [t for t in times if t >= end_ns]
        inside = [(t, dev) for t, dev in series if start_ns <= t <= end_ns]
        bracketed = sorted(set(before[-1:] + [t for t, _ in inside] + after[:1]))
        covered, _ = covered_ns(bracketed, start_ns, end_ns, gap)
        duration = end_ns-start_ns
        coverage = window_coverage(bracketed, start_ns, end_ns, gap)
        exceed = [t for t, dev in inside if not within_tolerance(dev, policy)]
        maxima = {key: max([dev[key] for _, dev in inside], default=0) for key in
                  ('joint_rad', 'position_m', 'rotation_rad', 'visual_scalar', 'discrete_mismatches')}
        rows.append(dict(station_id=station, samples_in_window=len(inside), duration_ns=duration,
                         covered_ns=covered, coverage=coverage, coverage_ok=coverage >= policy['min_coverage'],
                         bracketed=bool(before) and bool(after), exceedances=len(exceed),
                         first_exceedance_ns=str(exceed[0]) if exceed else None, maxima=maxima))
    return rows


class ContinuousTrace:
    """One sampler thread and adapter per station; runs for the audit's duration.

    Adapters expose ``registry`` and ``public_state()``; they must be separate
    objects from the command clients so each station is observed independently.
    """

    def __init__(self, samplers, baselines, policy, *, clock_ns=time.perf_counter_ns, sleep=time.sleep,
                 sample_sink=None):
        self.policy = validate_policy(policy)
        if set(samplers) != set(baselines) or len(samplers) < 2:
            raise ValueError('One sampler and baseline per station required')
        self.samplers, self.baselines = dict(samplers), dict(baselines)
        self.clock_ns, self.sleep, self.sample_sink = clock_ns, sleep, sample_sink
        self.period_ns = 1_000_000_000//self.policy['sample_hz']
        self.gap_ns = int(round(self.policy['max_gap_s']*1e9))
        self.settle_ns = int(round(self.policy['settle_s']*1e9))
        self.samples = {station: [] for station in samplers}
        self.counts = {station: dict(polls=0, fresh=0, stale=0) for station in samplers}
        self.identity = {station: None for station in samplers}
        self.errors = {}
        self.condition = threading.Condition()
        self.stopping = False
        self.threads = []

    def start(self, timeout_s=5.0):
        for station in self.samplers:
            thread = threading.Thread(target=self._run, args=(station,), daemon=True, name='trace-'+station)
            self.threads.append(thread)
            thread.start()
        deadline = time.monotonic()+timeout_s
        with self.condition:
            while not self.errors and not all(self.samples.values()):
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise RuntimeError('Continuous trace did not observe every station')
                self.condition.wait(remaining)
            if self.errors:
                raise RuntimeError('Continuous trace sampler failed: '+next(iter(self.errors.values())))

    def _run(self, station):
        adapter = self.samplers[station]
        due = self.clock_ns()
        try:
            while True:
                with self.condition:
                    if self.stopping:
                        return
                frame = validate_frame(adapter.public_state(), adapter.registry)
                received = self.clock_ns()
                identity = (frame['session_id'], frame['seq'])
                with self.condition:
                    self.counts[station]['polls'] += 1
                    previous = self.identity[station]
                    if previous is not None and previous[0] != identity[0]:
                        raise RuntimeError('Publisher session changed during trace (station restarted)')
                    if previous is None or identity[1] > previous[1]:
                        dev = deviation(self.baselines[station], frame)
                        self.samples[station].append((received, dev))
                        self.counts[station]['fresh'] += 1
                        self.identity[station] = identity
                        if self.sample_sink is not None:
                            self.sample_sink(dict(station_id=station, receipt_ns=str(received), seq=frame['seq'],
                                                  session_id=frame['session_id'], **dev))
                        self.condition.notify_all()
                    else:
                        self.counts[station]['stale'] += 1
                due += self.period_ns
                now = self.clock_ns()
                if due < now:
                    due = now  # Never burst to catch up; the gap is measured instead.
                self.sleep((due-now)/1e9)
        except Exception as error:
            with self.condition:
                self.errors[station] = type(error).__name__+': '+str(error)
                self.condition.notify_all()

    def window(self, target, start_ns, reply_ns):
        """Wait for a bracketing sample after reply+settle, then evaluate."""
        end_ns = reply_ns+self.settle_ns
        deadline = time.monotonic()+self.policy['settle_s']+2*self.policy['max_gap_s']+1.0
        others = [station for station in self.samplers if station != target]
        with self.condition:
            while not self.errors and not all(self.samples[s] and self.samples[s][-1][0] >= end_ns for s in others):
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(remaining)
            snapshot = {station: list(values) for station, values in self.samples.items()}
            errors = dict(self.errors)
        rows = evaluate_window(snapshot, target=target, start_ns=start_ns, end_ns=end_ns, policy=self.policy)
        return dict(target=target, start_ns=str(start_ns), end_ns=str(end_ns), stations=rows,
                    sampler_errors=errors)

    def close(self):
        with self.condition:
            self.stopping = True
            self.condition.notify_all()
        for thread in self.threads:
            thread.join(timeout=max(2.0, 4*self.policy['max_gap_s']))
        alive = [thread.name for thread in self.threads if thread.is_alive()]
        if alive:
            self.errors.setdefault('trace', 'Sampler threads did not stop: '+','.join(alive))
        return dict(counts=deepcopy(self.counts), errors=dict(self.errors))


def summarize(windows, policy, closing):
    """Aggregate per-window rows; passed only when coverage and tolerance hold everywhere."""
    policy = validate_policy(policy)
    pairs = [row for window in windows for row in window['stations']]
    duration = sum(row['duration_ns'] for row in pairs)
    covered = sum(row['covered_ns'] for row in pairs)
    errors = dict(closing['errors'])
    for window in windows:
        errors.update(window['sampler_errors'])
    exceedances = sum(row['exceedances'] for row in pairs)
    coverage_ok = bool(pairs) and all(row['coverage_ok'] for row in pairs)
    maxima = {key: max([row['maxima'][key] for row in pairs], default=0) for key in
              ('joint_rad', 'position_m', 'rotation_rad', 'visual_scalar', 'discrete_mismatches')}
    complete = bool(windows) and not errors
    return dict(continuous_trace_complete=complete,
                trace_passed=complete and coverage_ok and exceedances == 0,
                trace_policy=policy, trace_policy_sha256=policy_sha256(policy),
                trace_windows=len(windows), trace_window_station_pairs=len(pairs),
                trace_coverage_overall=covered/duration if duration else (1.0 if coverage_ok else 0.0),
                trace_min_window_coverage=min([row['coverage'] for row in pairs], default=0.0),
                trace_windows_below_min_coverage=sum(not row['coverage_ok'] for row in pairs),
                trace_tolerance_exceedances=exceedances, trace_max_observed=maxima,
                trace_sample_counts=closing['counts'], trace_errors=errors,
                undetectable_excursion_max_s=policy['max_gap_s'])
