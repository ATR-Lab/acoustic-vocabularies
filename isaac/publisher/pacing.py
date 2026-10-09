"""Deadline pacing and garbage-collector policy for the paced publisher loop.

Nothing here touches Isaac, USD or the wire schema. The loop calls a supplied
``advance()`` for its physics work, so the same code runs the live hour check
(`benchmark.run_publisher_check`) and the host-only synthetic harness
(`host_timing`).

Pacing modes
------------
``at_deadline`` (legacy): physics, sleep to the absolute deadline, then sample,
check, encode and stamp. The host stamp is taken after the state readback, so
readback-duration variation is added to every publish interval.

``presample``: physics, then sample, check and encode immediately, sleep to the
absolute deadline, then stamp and hand off. Only the simulation thread steps the
simulator and it does not step between sampling and stamping, so the published
state is still the current simulator state at the stamped host time. The stamp
therefore no longer carries readback/encode variation, only sleep wake-up and a
string join, unless the tick's work overruns its deadline.

Both modes use integer absolute deadlines (no drift accumulation), count missed
ticks and never publish catch-up bursts.
"""
from __future__ import annotations

import gc
import math
import time

NS_PER_S = 1_000_000_000
PACING_MODES = ("at_deadline", "presample")
GC_POLICIES = ("default", "freeze", "freeze_manual")
# Large enough that CPython never triggers an automatic generation-2 pass.
_NO_AUTOMATIC_GEN2 = 2**30


def deadline_ns(epoch_ns, index, rate_hz):
    """Absolute integer deadline of tick ``index``; no float accumulation."""
    return epoch_ns + index * NS_PER_S // rate_hz


def claim_deadline(epoch_ns, next_index, rate_hz, now_ns):
    """Return ``(due_index, missed)`` once tick ``next_index`` is due, else None.

    ``due_index`` is the latest tick whose deadline is at or before ``now_ns``.
    Ticks between ``next_index`` and ``due_index`` are counted as missed and are
    skipped: a single frame is published for the latest due tick, never a burst.
    """
    if now_ns < deadline_ns(epoch_ns, next_index, rate_hz):
        return None
    due = max(next_index, (now_ns - epoch_ns) * rate_hz // NS_PER_S)
    # Integer floor of the period can leave the next deadline exactly at now.
    while deadline_ns(epoch_ns, due + 1, rate_hz) <= now_ns:
        due += 1
    return due, due - next_index


def sleep_until(deadline, *, clock_ns=time.monotonic_ns, sleep=time.sleep, spin_ns=0):
    """Sleep to an absolute deadline; optionally spin for the final ``spin_ns``.

    Returns the wake-up lateness in nanoseconds (0 when already past). Spinning
    holds the GIL, so it is off by default and bounded by the caller.
    """
    if type(spin_ns) is not int or spin_ns < 0:
        raise ValueError("spin_ns must be a non-negative integer")
    now = clock_ns()
    if now >= deadline:
        return 0
    if deadline - now > spin_ns:
        sleep((deadline - now - spin_ns) / NS_PER_S)
    now = clock_ns()
    while now < deadline:
        now = clock_ns()
    return now - deadline


class GcMonitor:
    """Records collector pauses through ``gc.callbacks`` with bounded memory."""

    def __init__(self, clock_ns=time.perf_counter_ns, keep=20):
        self.clock_ns, self.keep = clock_ns, keep
        self.started = {}
        self.stats = {g: dict(events=0, total_ms=0.0, max_ms=0.0, collected=0) for g in (0, 1, 2)}
        self.longest = []
        self.installed = False

    def __call__(self, phase, info):
        generation = info["generation"]
        if phase == "start":
            self.started[generation] = self.clock_ns()
            return
        began = self.started.pop(generation, None)
        if began is None:
            return
        elapsed = (self.clock_ns() - began) / 1e6
        record = self.stats[generation]
        record["events"] += 1
        record["total_ms"] += elapsed
        record["max_ms"] = max(record["max_ms"], elapsed)
        record["collected"] += info["collected"]
        self.longest.append(dict(generation=generation, elapsed_ms=elapsed, collected=info["collected"]))
        self.longest.sort(key=lambda item: -item["elapsed_ms"])
        del self.longest[self.keep:]

    def install(self):
        if not self.installed:
            gc.callbacks.append(self)
            self.installed = True

    def remove(self):
        if self.installed:
            if self in gc.callbacks:
                gc.callbacks.remove(self)
            self.installed = False

    def summary(self):
        return dict(by_generation={str(g): dict(v) for g, v in self.stats.items()},
                    longest_pauses=[dict(item) for item in self.longest])


class GcPolicy:
    """Collector policy applied only for the measured publish loop.

    ``default``: unchanged interpreter behavior (pauses are still recorded).
    ``freeze``: after warm-up, one full collection then ``gc.freeze()``. The
    long-lived warm-up heap (Kit/USD/torch objects) moves to the permanent
    generation, so later automatic generation-2 passes traverse only objects
    that survived since the freeze. Automatic collection remains enabled.
    ``freeze_manual``: ``freeze`` plus no automatic generation-2 pass; an
    explicit ``gc.collect(2)`` runs at a publish-loop safe point every
    ``manual_interval`` publications, immediately after a frame is handed off.

    Risk: cyclic garbage formed among frozen objects is not reclaimed until
    ``stop()`` unfreezes them; ``freeze_manual`` additionally defers cycles in
    new objects to the next safe point. Both are bounded by the run length.
    """

    def __init__(self, mode="default", *, manual_interval=1800, monitor=True,
                 gc_module=gc, clock_ns=time.perf_counter_ns):
        if mode not in GC_POLICIES:
            raise ValueError("Unknown GC policy")
        if type(manual_interval) is not int or manual_interval < 1:
            raise ValueError("manual_interval must be a positive integer")
        self.mode, self.manual_interval, self.gc = mode, manual_interval, gc_module
        self.clock_ns = clock_ns
        self.monitor = GcMonitor(clock_ns) if monitor else None
        self.active = False
        self.saved_threshold = None
        self.preexisting_frozen = 0
        self.frozen_objects = 0
        self.warmup_collect_ms = None
        self.safe_points = 0
        self.manual_collections = []

    def start(self):
        if self.active:
            raise RuntimeError("GC policy already active")
        self.saved_threshold = self.gc.get_threshold()
        # gc.unfreeze() releases everything in the permanent generation. If
        # another component froze objects first, leave the freeze in place.
        self.preexisting_frozen = self.gc.get_freeze_count()
        if self.mode in ("freeze", "freeze_manual"):
            began = self.clock_ns()
            self.gc.collect()
            self.gc.freeze()
            self.warmup_collect_ms = (self.clock_ns() - began) / 1e6
            self.frozen_objects = self.gc.get_freeze_count()
        if self.mode == "freeze_manual":
            first, second, _ = self.saved_threshold
            self.gc.set_threshold(first, second, _NO_AUTOMATIC_GEN2)
        if self.monitor is not None:
            self.monitor.install()
        self.active = True

    def safe_point(self):
        """Call right after a publication; runs the manual pass when due."""
        if not self.active or self.mode != "freeze_manual":
            return None
        self.safe_points += 1
        if self.safe_points % self.manual_interval:
            return None
        began = self.clock_ns()
        collected = self.gc.collect(2)
        elapsed = (self.clock_ns() - began) / 1e6
        if len(self.manual_collections) < 4096:
            self.manual_collections.append(dict(elapsed_ms=elapsed, collected=collected))
        return elapsed

    def stop(self):
        if not self.active:
            return
        try:
            if self.monitor is not None:
                self.monitor.remove()
            if self.saved_threshold is not None:
                self.gc.set_threshold(*self.saved_threshold)
            if self.mode in ("freeze", "freeze_manual") and not self.preexisting_frozen:
                self.gc.unfreeze()
        finally:
            self.active = False

    def summary(self):
        manual = [item["elapsed_ms"] for item in self.manual_collections]
        return dict(policy=self.mode, saved_threshold=list(self.saved_threshold or ()),
                    frozen_objects=self.frozen_objects, preexisting_frozen=self.preexisting_frozen,
                    warmup_collect_ms=self.warmup_collect_ms,
                    manual_interval=self.manual_interval if self.mode == "freeze_manual" else None,
                    manual_collections=len(manual), manual_collection_max_ms=max(manual, default=None),
                    monitor=self.monitor.summary() if self.monitor is not None else None)


def run_paced_loop(publisher, advance, *, seconds, pacing="presample", gc_policy=None,
                   clock_ns=time.monotonic_ns, sleep=time.sleep, spin_ns=0, check=None, stats=None):
    """Publish at absolute deadlines until ``seconds`` of host time elapse.

    ``advance()`` performs one tick of simulation work on this thread and
    returns ``(sim_time, sim_step)``. ``check()`` may raise to stop the run
    (for example a failed receiver). Returns ``(start_ns, end_ns, stats)``;
    exceptions propagate after the GC policy is restored, and the measured
    start remains available as ``publisher.epoch_ns``.
    """
    if pacing not in PACING_MODES:
        raise ValueError("Unknown pacing mode")
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("Positive finite run duration required")
    stats = stats if stats is not None else {}
    stats.update(ticks=0, processing_ms_max=0.0, wake_late_ms_max=0.0, overrun_ticks=0)
    if gc_policy is not None:
        gc_policy.start()
    try:
        start = limit = None
        while start is None or clock_ns() < limit:
            began = clock_ns()
            sim_time, sim_step = advance()
            if pacing == "presample" and publisher.prepare(sim_time, sim_step) is None:
                raise RuntimeError(publisher.fault or "PUBLISH_PREPARE_FAILURE")
            ready = clock_ns()
            if start is None:
                # The measured window opens when the first tick's pre-deadline
                # work is done. Otherwise tick 0's deadline precedes its own
                # physics and the next several ticks start behind schedule: a
                # transient that is negligible in an hour but can set p99 in a
                # 12-20 second diagnostic. Every later deadline is absolute.
                start = began = ready
                publisher.epoch_ns, publisher.deadline_index = start, 0
                limit = start + int(seconds * NS_PER_S)
            deadline = publisher.next_deadline_ns()
            late = sleep_until(deadline, clock_ns=clock_ns, sleep=sleep, spin_ns=spin_ns)
            if pacing == "presample":
                frame = publisher.publish_prepared(sim_time, sim_step)
            else:
                frame = publisher.after_step(sim_time, sim_step)
            published = clock_ns()
            if frame is None or publisher.fault:
                raise RuntimeError(publisher.fault or "PUBLISH_FAILURE")
            if check is not None:
                check()
            # Work not finished by the deadline (presample: physics+sample+encode;
            # at_deadline: physics, since its sampling starts after the deadline).
            if ready > deadline:
                stats["overrun_ticks"] += 1
            stats["ticks"] += 1
            # Active work only (excludes the sleep): physics, sampling/encoding
            # and the post-wake stamp/handoff.
            active = (ready - began) + (published - max(ready, deadline))
            stats["processing_ms_max"] = max(stats["processing_ms_max"], active / 1e6)
            stats["wake_late_ms_max"] = max(stats["wake_late_ms_max"], late / 1e6)
            if gc_policy is not None:
                gc_policy.safe_point()
        end = clock_ns()
    finally:
        if gc_policy is not None:
            gc_policy.stop()
    return start, end, stats
