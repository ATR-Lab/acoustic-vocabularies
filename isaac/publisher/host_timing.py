"""Host-only synthetic timing harness for the paced publisher loop (no Isaac).

SYNTHETIC, HOST-ONLY, NOT QUALIFICATION. Both modes run the production
``StatePublisher`` (projection, validation, encoding, sequence, deadline
accounting, evidence log) and the same ``run_paced_loop`` used by the live hour
check. Only the simulator is replaced, by a synthetic source whose physics and
readback costs are drawn from a seeded model calibrated to retained Isaac stage
timings. Nothing here measures Isaac, PhysX, USD, the GPU, the remote host's
scheduler or a socket receiver. Every run is written with
``source_kind: synthetic`` so the analyzer can never pass the rate screen.

``virtual`` mode advances a virtual clock by the modeled costs and a modeled
sleep wake-up latency. It is deterministic for a seed, independent of host load,
and isolates what the pacing logic does with component-time variation over a
full virtual hour. Real GC pauses do not advance virtual time.

``wall`` mode busy-waits the modeled costs on the real clock with a strict
validating receiver thread and a long-lived heap ballast, so interpreter GC,
GIL hand-off and host sleep behavior are real (and host-specific).

Example (repository root)::

    python -m isaac.publisher.host_timing --output raw-results/host-timing
"""
from __future__ import annotations

import argparse
from collections import deque
import csv
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import queue
import random
import statistics
import sys
import tempfile
import threading
import time
import tracemalloc

from .analyze import analyze, percentile
from .benchmark import registry_from_snapshot
from .pacing import GcPolicy, run_paced_loop
from .protocol import strict_loads, validate_frame
from .runtime import StatePublisher

ROOT = Path(__file__).resolve().parents[2]
CONFIGS = {
    # name: (pacing, gc policy)
    "legacy": ("at_deadline", "default"),
    "presample": ("presample", "default"),
    "presample_freeze": ("presample", "freeze"),
    "presample_freeze_manual": ("presample", "freeze_manual"),
    "legacy_freeze": ("at_deadline", "freeze"),
}


def host_clock():
    """Nanosecond clock with adequate resolution on this host.

    Windows CPython before 3.13 implements time.monotonic with GetTickCount64
    (15.6 ms resolution), so the harness uses perf_counter there.
    """
    if time.get_clock_info("monotonic").resolution <= 1e-6:
        return "monotonic", time.monotonic_ns
    return "perf_counter", time.perf_counter_ns


def spin(ns, clock_ns=time.perf_counter_ns):
    """Busy-wait, holding the GIL like Python-bound USD readback does."""
    end = clock_ns() + ns
    while clock_ns() < end:
        pass


class VirtualClock:
    """Deterministic clock: costs and sleeps advance it, nothing else does."""

    def __init__(self, rng, wake_us=60., wake_jitter_us=20., wake_tail_probability=.001,
                 wake_tail_us=(200., 1000.)):
        self.now = 1_000_000_000
        self.rng = rng
        self.wake_us, self.wake_jitter_us = wake_us, wake_jitter_us
        self.wake_tail_probability, self.wake_tail_us = wake_tail_probability, wake_tail_us

    def clock_ns(self):
        return self.now

    def burn(self, ns):
        self.now += ns

    def sleep(self, seconds):
        late = self.wake_us + self.rng.expovariate(1/self.wake_jitter_us)
        if self.rng.random() < self.wake_tail_probability:
            late += self.rng.uniform(*self.wake_tail_us)
        # At least 1 us of wake-up latency also guarantees forward progress.
        self.now += round(seconds * 1e9) + max(1000, int(late * 1000))

    def describe(self):
        return dict(wake_model="wake_us + Exp(wake_jitter_us) + Bernoulli(p)*U(wake_tail_us)",
                    wake_us=self.wake_us, wake_jitter_us=self.wake_jitter_us,
                    wake_tail_probability=self.wake_tail_probability, wake_tail_us=list(self.wake_tail_us))


class CostModel:
    """Seeded per-call costs: base + exponential jitter + rare uniform tail.

    Calibration (assumptions, not remote-host measurements): one physics step
    including the actuator flush and articulation update about 7.3 ms median
    (receiver-process hour disconnect phases, 7.29-7.49 ms); public readback
    about 11 ms median with 16-19 ms maxima in 12-second phases
    (encoder-projection-results.json, unprotected public_read 11.2-11.6 ms
    median); encoding about 1 ms (projection follow-up candidate).
    """

    def __init__(self, seed=54, physics_ms=7.3, physics_jitter_ms=.3, read_ms=11.0,
                 read_jitter_ms=.6, tail_probability=.01, tail_ms=(2., 6.), encode_ms=1.0):
        self.rng = random.Random(seed)
        self.physics_ms, self.physics_jitter_ms = physics_ms, physics_jitter_ms
        self.read_ms, self.read_jitter_ms = read_ms, read_jitter_ms
        self.tail_probability, self.tail_ms = tail_probability, tail_ms
        self.encode_ms = encode_ms

    def _draw(self, base, jitter):
        value = base + (self.rng.expovariate(1/jitter) if jitter > 0 else 0.)
        if self.tail_probability and self.rng.random() < self.tail_probability:
            value += self.rng.uniform(*self.tail_ms)
        return int(value * 1e6)

    def physics_ns(self):
        return self._draw(self.physics_ms, self.physics_jitter_ms)

    def read_ns(self):
        return self._draw(self.read_ms, self.read_jitter_ms)

    def describe(self):
        return dict(cost_model="base + Exp(jitter) + Bernoulli(tail_probability)*U(tail_ms)",
                    physics_ms_per_step=self.physics_ms, physics_jitter_ms=self.physics_jitter_ms,
                    read_ms=self.read_ms, read_jitter_ms=self.read_jitter_ms,
                    tail_probability=self.tail_probability, tail_ms=list(self.tail_ms),
                    virtual_encode_ms=self.encode_ms)


class SyntheticWorkcell:
    """Synthetic state source; fresh containers per sample like the USD reader."""

    def __init__(self, neutral_state, costs, burn, *, steps_per_tick=2, extra_sample_ns=0,
                 churn_per_tick=0, churn_ticks=90):
        self.neutral = neutral_state
        self.costs, self.burn, self.steps_per_tick = costs, burn, steps_per_tick
        self.extra_sample_ns = extra_sample_ns
        self.sim_step, self.sim_time = 0, 0.
        self.churn_per_tick = churn_per_tick
        self.survivors = deque(maxlen=max(1, churn_ticks))

    def advance(self):
        for _ in range(self.steps_per_tick):
            self.burn(self.costs.physics_ns())
            self.sim_step += 1
        self.sim_time = self.sim_step / 60
        if self.churn_per_tick:
            # Objects that live a few seconds survive young collections and are
            # promoted; their accumulation is what triggers full collections.
            self.survivors.append([[index] for index in range(self.churn_per_tick)])
        return self.sim_time, self.sim_step

    def sample(self):
        self.burn(self.costs.read_ns() + self.extra_sample_ns)
        robot = self.neutral["robot"]["joint_positions_rad"]
        offset = (self.sim_step % 600) * 1e-6
        positions = [value + offset for value in robot]
        objects = {key: {"position_m": list(record["position_m"]),
                         "rotation_xyzw": list(record["rotation_xyzw"]),
                         "visible": record["visible"], "enabled": record["enabled"],
                         "state": dict(record["state"])}
                   for key, record in self.neutral["objects"].items()}
        return positions, objects, None


class NullTransport:
    def __init__(self):
        self.count = 0
        self.overwrites = 0
        self.sequence_gaps = 0
        self.error = None

    def submit(self, payload):
        self.count += 1

    def metrics(self):
        return dict(connected_clients=0, queue_overwrites=0, failed=False)

    def drain(self, published, timeout=0.):
        pass

    def close(self):
        pass


class InProcessReceiver:
    """Transport stand-in: one-slot mailbox and a strict validating thread.

    Mirrors WebSocketTransport's submit/metrics/close surface and the
    in-simulator LocalCollector's GIL competition (strict_loads +
    validate_frame on every frame). It does not open sockets.
    """

    def __init__(self, registry, validate=True):
        self.registry, self.validate = registry, validate
        self.mailbox = queue.Queue(maxsize=1)
        self.lock = threading.Lock()
        self.overwrites = self.count = self.sequence_gaps = 0
        self.last_seq = None
        self.error = None
        self.closed = False
        self.thread = threading.Thread(target=self._run, name="synthetic-receiver", daemon=True)
        self.thread.start()

    def _run(self):
        try:
            while True:
                payload = self.mailbox.get()
                if payload is None:
                    return
                frame = strict_loads(payload)
                if self.validate:
                    validate_frame(frame, self.registry)
                if self.last_seq is not None and frame["seq"] != self.last_seq + 1:
                    self.sequence_gaps += 1
                self.last_seq = frame["seq"]
                self.count += 1
        except Exception as error:
            self.error = error

    def submit(self, payload):
        if self.closed or self.error:
            raise RuntimeError("Synthetic receiver unavailable")
        with self.lock:
            try:
                self.mailbox.put_nowait(payload)
            except queue.Full:
                try:
                    self.mailbox.get_nowait()
                    self.overwrites += 1
                except queue.Empty:
                    pass
                self.mailbox.put_nowait(payload)

    def metrics(self):
        return dict(connected_clients=1, queue_overwrites=self.overwrites, failed=self.error is not None)

    def drain(self, published, timeout=2.):
        limit = time.monotonic() + timeout
        while self.count + self.overwrites < published and self.error is None and time.monotonic() < limit:
            time.sleep(.005)

    def close(self):
        if not self.closed:
            self.closed = True
            self.mailbox.put(None)
            self.thread.join(timeout=5)


def load_fixture(station_id="synthetic-host"):
    layout = json.loads((ROOT/"apparatus/workcell_layout.json").read_text(encoding="utf-8"))
    snapshot = json.loads((ROOT/"isaac/snapshots/neutral_v1.json").read_text(encoding="utf-8"))
    registry = registry_from_snapshot(layout, snapshot, "0"*64, station_id,
                                      ROOT/"docs/spikes/isaac/joint_inventory.csv")
    return registry, snapshot["state"]


def build_ballast(objects):
    """Long-lived tracked containers standing in for Kit/USD/torch heap objects."""
    return [[index] for index in range(objects)]


def full_collection_ms(repeats=3):
    values = []
    for _ in range(repeats):
        began = time.perf_counter_ns()
        gc.collect()
        values.append((time.perf_counter_ns() - began) / 1e6)
    return statistics.median(values)


def gc_pause_probe(ballast_objects, batches=600, batch=1000):
    """Automatic generation-2 pauses under each policy with a large live heap.

    Outside any timing loop: builds the ballast, applies a policy, then
    allocates surviving containers until CPython's own heuristics trigger
    full collections (or the batch cap is reached). Pause durations are real
    host measurements; they show what a full pass costs with and without the
    warm-up heap frozen, not what Isaac's heap costs.
    """
    from .pacing import GC_POLICIES
    results = {}
    ballast = build_ballast(ballast_objects)
    try:
        for mode in GC_POLICIES:
            gc.unfreeze()
            gc.collect()
            policy = GcPolicy(mode, manual_interval=1)
            keep = []
            policy.start()
            try:
                for _ in range(batches):
                    keep.append([[index] for index in range(batch)])
                manual_ms = policy.safe_point()
            finally:
                policy.stop()
                gc.unfreeze()
            stats = policy.summary()["monitor"]["by_generation"]["2"]
            results[mode] = dict(surviving_objects_allocated=batches*batch,
                                 automatic_gen2_events=stats["events"] - (1 if manual_ms is not None else 0),
                                 gen2_max_ms=stats["max_ms"], gen2_total_ms=stats["total_ms"],
                                 manual_collection_ms=manual_ms,
                                 warmup_collect_ms=policy.warmup_collect_ms)
            del keep
    finally:
        del ballast
        gc.collect()
    return dict(ballast_objects=ballast_objects, results=results)


def encode_stage_probe(registry, neutral, repeats=2000):
    """Median/p99 of the non-USD sample+encode stages on this host.

    Covers the accessor's public-state validation, the encoder projection,
    ``validate_frame``, JSON encoding, the complete ``prepare`` and the stamp
    join. USD attribute reads and the articulation copy need the simulator and
    are timed per frame by the live harness (``stages.csv``).
    """
    from copy import deepcopy
    from .protocol import StateEncoder, encode, validate_frame
    from isaac.workcell.state import _validate_states
    layout = json.loads((ROOT/"apparatus/workcell_layout.json").read_text(encoding="utf-8"))
    public = {key: {field: value[field] for field in ("position_m", "rotation_xyzw", "visible", "enabled", "state")}
              for key, value in neutral["objects"].items()}
    positions = list(neutral["robot"]["joint_positions_rad"])
    encoder = StateEncoder(registry, "synthetic")
    frame = encoder._frame(positions, public, .1, 1, lambda: "0")
    prepared = encoder.prepare(positions, public, .1, 1)
    stages = {
        "fresh_containers_deepcopy": lambda: deepcopy(public),
        "accessor_public_validation": lambda: _validate_states(layout, public, public_only=True),
        "encoder_projection": lambda: encoder._frame(positions, public, .1, 1, lambda: "0"),
        "validate_frame": lambda: validate_frame(frame, registry),
        "json_encode": lambda: encode(frame),
        "prepare_total": lambda: encoder.prepare(positions, public, .1, 1),
        "stamp_join": lambda: prepared.head + '"host_monotonic_ns":"1"' + prepared.tail,
    }
    result = {}
    for name, call in stages.items():
        values = []
        for _ in range(repeats):
            began = time.perf_counter_ns()
            call()
            values.append((time.perf_counter_ns() - began) / 1e6)
        result[name] = dict(p50=percentile(values, .5), p99=percentile(values, .99))
    return dict(repeats=repeats, objects=len(public), joints=len(positions), stages_ms=result)


def allocation_probe(registry, neutral, frames=300):
    """Per-frame allocation of the publish path with collection disabled.

    Reports net GC-tracked containers left per frame (expected ~0: frames are
    acyclic and freed by reference counting), net traced bytes per frame and
    the transient traced peak while one frame is built.
    """
    costs = CostModel(physics_ms=0, physics_jitter_ms=0, read_ms=0, read_jitter_ms=0, tail_probability=0)
    source = SyntheticWorkcell(neutral, costs, lambda ns: None, steps_per_tick=1)
    tick = [0]
    result = {}
    with tempfile.TemporaryDirectory(prefix="av-alloc-") as directory:
        for mode in ("at_deadline", "presample"):
            publisher = StatePublisher(registry, source.sample, NullTransport(), Path(directory)/f"{mode}.csv",
                                       source_kind="synthetic", clock_ns=lambda: tick[0])
            publisher.epoch_ns = 0
            enabled = gc.isenabled()
            gc.disable()
            tracemalloc.start()
            try:
                def one():
                    sim_time, step = source.advance()
                    tick[0] = publisher.next_deadline_ns()
                    if mode == "presample":
                        publisher.prepare(sim_time, step)
                        frame = publisher.publish_prepared(sim_time, step)
                    else:
                        frame = publisher.after_step(sim_time, step)
                    if frame is None:
                        raise RuntimeError(publisher.fault)
                for _ in range(20):
                    one()
                time.sleep(.05)  # let the evidence writer drain queued rows
                before_count = gc.get_count()[0]
                before_mem = tracemalloc.get_traced_memory()[0]
                peaks = []
                for _ in range(frames):
                    tracemalloc.reset_peak()
                    base = tracemalloc.get_traced_memory()[0]
                    one()
                    peaks.append(tracemalloc.get_traced_memory()[1] - base)
                time.sleep(.05)
                after_count = gc.get_count()[0]
                after_mem = tracemalloc.get_traced_memory()[0]
            finally:
                tracemalloc.stop()
                if enabled:
                    gc.enable()
                publisher.close()
            result[mode] = dict(frames=frames,
                                net_tracked_containers_per_frame=(after_count-before_count)/frames,
                                net_traced_bytes_per_frame=(after_mem-before_mem)/frames,
                                transient_peak_bytes_median=statistics.median(peaks))
    return result


def summarize(rows, rate_hz):
    stamps = [int(row["host_monotonic_ns"]) for row in rows]
    errors = [abs((b-a)/1e6 - 1000/rate_hz) for a, b in zip(stamps, stamps[1:])]
    serialize = [float(row["serialize_ms"]) for row in rows]
    return (dict({label: percentile(errors, q) for label, q in
                  (("p50", .5), ("p90", .9), ("p99", .99), ("p99_9", .999))}, max=max(errors, default=None),
                 over_limit=sum(e > 1000/rate_hz*.1 for e in errors), intervals=len(errors)),
            dict(p50=percentile(serialize, .5), p99=percentile(serialize, .99), max=max(serialize, default=None)))


def run_config(name, mode, registry, neutral, output, *, seconds, rate_hz, seed, cost_args, wall_clock,
               churn_per_tick, validate, spin_us, manual_interval):
    pacing, policy_name = CONFIGS[name]
    directory = output/name
    directory.mkdir(parents=True, exist_ok=False)
    costs = CostModel(seed, **cost_args)
    if mode == "virtual":
        clock = VirtualClock(random.Random(seed + 7919))
        clock_ns, sleep, burn = clock.clock_ns, clock.sleep, clock.burn
        source = SyntheticWorkcell(neutral, costs, burn, extra_sample_ns=int(costs.encode_ms*1e6))
        receiver = NullTransport()
        policy = None
        if policy_name != "default":
            raise ValueError("GC policies are only meaningful in wall mode")
    else:
        clock_ns, sleep = wall_clock, time.sleep
        source = SyntheticWorkcell(neutral, costs, lambda ns: spin(ns, wall_clock), churn_per_tick=churn_per_tick)
        receiver = InProcessReceiver(registry, validate=validate)
        policy = GcPolicy(policy_name, manual_interval=manual_interval)
    publisher = StatePublisher(registry, source.sample, receiver, directory/"publish.csv",
                               rate_hz=rate_hz, source_kind="synthetic", clock_ns=clock_ns)
    stats, failure, end = {}, None, None
    try:
        def check():
            if receiver.error:
                raise RuntimeError("SYNTHETIC_RECEIVER_FAILURE")
        _, end, _ = run_paced_loop(publisher, source.advance, seconds=seconds, pacing=pacing,
                                   gc_policy=policy, clock_ns=clock_ns, sleep=sleep,
                                   spin_ns=spin_us*1000 if mode == "wall" else 0,
                                   check=check, stats=stats)
    except Exception as error:
        failure = type(error).__name__ + ": " + str(error)
    finally:
        end = end or clock_ns()
        receiver.drain(publisher.published)
        publisher.close()
        receiver.close()
        if mode == "wall" and gc.get_freeze_count():
            # GcPolicy leaves a freeze in place when the interpreter already had
            # one (CPython 3.12 can start with frozen objects). This harness
            # owns its process, so it unfreezes so later configs start equal.
            gc.unfreeze()
    metadata = dict(rate_hz=rate_hz, start_host_ns=str(publisher.epoch_ns), end_host_ns=str(end),
                    completed=failure is None, source_kind="synthetic",
                    schema_validated_frames=receiver.count if (mode == "wall" and validate) else 0, fault=failure)
    (directory/"metadata.json").write_text(json.dumps(metadata, indent=2)+"\n", encoding="utf-8")
    summary = analyze(directory/"publish.csv", metadata, required_seconds=seconds)
    with (directory/"publish.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    errors, serialize = summarize(rows, rate_hz)
    # The first interval includes start-up (frame 0's deadline is the start
    # instant, before any physics). Reported separately; the analyzer keeps it.
    steady, _ = summarize(rows[1:], rate_hz)
    return dict(config=name, mode=mode, pacing=pacing, gc_policy=policy_name, seed=seed, fault=failure,
                frames=len(rows), received=receiver.count, receiver_sequence_gaps=receiver.sequence_gaps,
                receiver_error=None if receiver.error is None else repr(receiver.error),
                queue_overwrites=receiver.overwrites,
                interval_error_ms=errors, interval_error_ms_excluding_first=steady,
                sample_encode_ms=serialize,
                analyzer={k: summary[k] for k in (
                    "absolute_period_error_p99_ms", "max_gap_ms", "missed_deadlines", "queue_overwrites",
                    "timing_screen", "rate_screen", "deadline_lag_median_ms", "deadline_lag_p99_ms",
                    "deadline_lag_max_ms") if k in summary},
                loop=dict(stats), gc=policy.summary() if policy is not None else None,
                raw_csv_sha256=summary["raw_csv_sha256"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--virtual-seconds", type=float, default=3600.,
                        help="Virtual-clock duration per config (0 skips virtual mode)")
    parser.add_argument("--virtual-configs", default="legacy,presample")
    parser.add_argument("--seconds", type=float, default=60., help="Wall-clock duration per config (0 skips)")
    parser.add_argument("--configs", default="legacy,presample,presample_freeze,presample_freeze_manual")
    parser.add_argument("--repeats", type=int, default=2, help="Wall-clock repeats; order alternates (ABBA)")
    parser.add_argument("--rate-hz", type=int, choices=(30, 60), default=30)
    parser.add_argument("--seed", type=int, default=54)
    parser.add_argument("--ballast-objects", type=int, default=1_500_000,
                        help="Wall mode: long-lived tracked objects standing in for the simulator heap")
    parser.add_argument("--churn-per-tick", type=int, default=300,
                        help="Wall mode: short-lived surviving containers per tick (drives promotions)")
    parser.add_argument("--no-validate", action="store_true", help="Wall-mode receiver skips validate_frame")
    parser.add_argument("--spin-us", type=int, default=0)
    parser.add_argument("--manual-interval", type=int, default=1800)
    parser.add_argument("--no-gc-probe", action="store_true", help="Skip the isolated GC pause probe")
    parser.add_argument("--physics-ms", type=float, default=7.3)
    parser.add_argument("--read-ms", type=float, default=11.0)
    parser.add_argument("--read-jitter-ms", type=float, default=.6)
    parser.add_argument("--tail-probability", type=float, default=.01)
    args = parser.parse_args(argv)
    virtual = [n.strip() for n in args.virtual_configs.split(",") if n.strip()] if args.virtual_seconds else []
    wall = [n.strip() for n in args.configs.split(",") if n.strip()] if args.seconds else []
    if any(n not in CONFIGS for n in virtual + wall) or any(CONFIGS[n][1] != "default" for n in virtual):
        parser.error("Unknown config, or a GC policy requested in virtual mode")
    for value in (args.virtual_seconds, args.seconds):
        if not math.isfinite(value) or not (value == 0 or 2 <= value <= 3600):
            parser.error("Durations must be 0 or 2..3600 seconds")
    args.output.mkdir(parents=True, exist_ok=False)
    registry, neutral = load_fixture()
    clock_name, wall_clock = host_clock()
    cost_args = dict(physics_ms=args.physics_ms, read_ms=args.read_ms, read_jitter_ms=args.read_jitter_ms,
                     tail_probability=args.tail_probability)
    report = dict(kind="publisher_host_timing", synthetic_host_only=True, qualification=False,
                  label="SYNTHETIC HOST-ONLY TIMING; NOT ISAAC, NOT THE REMOTE HOST, NOT QUALIFICATION",
                  host=dict(platform=platform.platform(), python=sys.version.split()[0],
                            implementation=platform.python_implementation(), cpu_count=os.cpu_count(),
                            wall_clock=clock_name, switch_interval_s=sys.getswitchinterval(),
                            gc_threshold=list(gc.get_threshold())),
                  parameters=dict(rate_hz=args.rate_hz, seed=args.seed,
                                  cost_model=CostModel(args.seed, **cost_args).describe(),
                                  virtual=dict(seconds=args.virtual_seconds, configs=virtual,
                                               clock=VirtualClock(None).describe()),
                                  wall=dict(seconds=args.seconds, configs=wall, repeats=args.repeats,
                                            ballast_objects=args.ballast_objects,
                                            churn_per_tick=args.churn_per_tick,
                                            receiver_validates=not args.no_validate, spin_us=args.spin_us,
                                            manual_interval=args.manual_interval)),
                  allocation_probe=allocation_probe(registry, neutral),
                  encode_stage_probe=encode_stage_probe(registry, neutral), virtual_runs=[], wall_runs=[])
    if not args.no_gc_probe and args.ballast_objects:
        report["interpreter_frozen_at_start"] = gc.get_freeze_count()
        report["gc_pause_probe"] = gc_pause_probe(args.ballast_objects)
    common = dict(rate_hz=args.rate_hz, cost_args=cost_args, wall_clock=wall_clock,
                  churn_per_tick=args.churn_per_tick, validate=not args.no_validate,
                  spin_us=args.spin_us, manual_interval=args.manual_interval)

    def show(result):
        print(json.dumps(dict(mode=result["mode"], config=result["config"],
                              p99_ms=round(result["interval_error_ms"]["p99"], 4),
                              max_ms=round(result["interval_error_ms"]["max"], 4),
                              missed=result["analyzer"]["missed_deadlines"])), flush=True)

    for index, name in enumerate(virtual):
        # Same seed for every virtual config: identical cost sequences.
        result = run_config(name, "virtual", registry, neutral, args.output/f"virtual-{index:02d}",
                            seconds=args.virtual_seconds, seed=args.seed, **common)
        report["virtual_runs"].append(result)
        show(result)
    if wall:
        report.setdefault("interpreter_frozen_at_start", gc.get_freeze_count())
        gc.unfreeze()  # every wall config starts from the same unfrozen heap
        ballast = build_ballast(args.ballast_objects)
        report["ballast_full_collection_ms"] = full_collection_ms()
        order = []
        for repeat in range(args.repeats):
            order.extend(wall if repeat % 2 == 0 else list(reversed(wall)))
        for index, name in enumerate(order):
            result = run_config(name, "wall", registry, neutral, args.output/f"wall-{index:02d}",
                                seconds=args.seconds, seed=args.seed + index, **common)
            result["order"] = index
            report["wall_runs"].append(result)
            show(result)
        del ballast
    text = json.dumps(report, indent=2, allow_nan=False) + "\n"
    (args.output/"host-timing-summary.json").write_text(text, encoding="utf-8")
    print("summary_sha256", hashlib.sha256(text.encode()).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
