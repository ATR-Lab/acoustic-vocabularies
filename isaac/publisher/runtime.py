"""Simulation-thread scheduler. Transport and disk workers never step Isaac."""
from __future__ import annotations

import csv
import os
import queue
import threading
import time

from .pacing import claim_deadline, deadline_ns
from .protocol import StateEncoder, encode


class PublishLog:
    FIELDS = ("seq", "host_monotonic_ns", "sim_step", "sim_time", "serialize_ms",
              "handoff_ms", "missed_deadlines", "connected_clients", "queue_overwrites",
              "deadline_lag_ms")

    def __init__(self, path):
        # Exclusive create prevents accidentally overwriting evidence.
        self.stream = open(path, "x", encoding="utf-8", newline="")
        self.queue = queue.Queue(maxsize=4096)
        self.error = None
        self.thread = threading.Thread(target=self._run, name="publisher-evidence", daemon=True)
        self.thread.start()

    def _run(self):
        try:
            writer = csv.DictWriter(self.stream, fieldnames=self.FIELDS)
            writer.writeheader()
            while True:
                row = self.queue.get()
                if row is None:
                    break
                writer.writerow(row)
            self.stream.flush()
            os.fsync(self.stream.fileno())
        except Exception as error:
            self.error = error
        finally:
            self.stream.close()

    def offer(self, row):
        if self.error or not self.thread.is_alive():
            raise RuntimeError("Publisher evidence writer failed") from self.error
        self.queue.put_nowait(row)

    def close(self):
        if self.thread.is_alive():
            self.queue.put(None, timeout=10)
            self.thread.join(timeout=10)
        if self.thread.is_alive() or self.error:
            raise RuntimeError("Publisher evidence did not finalize") from self.error


class _Prepared:
    __slots__ = ("encoded", "sim_time", "sim_step", "complete", "observed_ns", "valid", "serialize_ns")

    def __init__(self, encoded, sim_time, sim_step, complete, observed_ns, valid, serialize_ns):
        self.encoded, self.sim_time, self.sim_step = encoded, sim_time, sim_step
        self.complete, self.observed_ns, self.valid = complete, observed_ns, valid
        self.serialize_ns = serialize_ns


class StatePublisher:
    """Call after_step after Isaac's articulation/object readback, on its thread.

    sample() returns (canonical_positions, object_records, complete_reset_state).
    neutral_check(complete_reset_state) returns bool or a reset_ok reply without
    writing or stepping. Only a literal True qualifies.
    A missed deadline is logged and skipped; no catch-up/fabricated frames.

    Two call patterns produce identical wire frames:

    - ``after_step``: when a deadline is due, sample/check/encode and stamp.
    - ``prepare`` then ``publish_prepared``: sample/check/encode first (without
      consuming a sequence number), then at the deadline stamp and hand off.
      The caller must not step or write the simulator in between;
      ``publish_prepared`` rejects different simulation counters.
    """
    def __init__(self, registry, sample, transport, log_path, *, rate_hz=30,
                 neutral_check=None, source_kind="live", clock_ns=time.monotonic_ns, timing=None,
                 observation=None):
        if type(rate_hz) is not int or rate_hz not in (30, 60):
            raise ValueError("Engineering rates are 30 or 60 Hz; final selection remains provisional")
        self.encoder = StateEncoder(registry, source_kind, clock_ns)
        self.sample, self.transport, self.clock_ns = sample, transport, clock_ns
        self.timing = timing
        self.observation = observation
        self.rate_hz, self.neutral_check = rate_hz, neutral_check
        self.protected = False
        self.fault = None
        self.log = PublishLog(log_path)
        self.epoch_ns, self.deadline_index = clock_ns(), 0
        self.last_publish_ns = None
        self.published = self.missed = 0
        self.closed = False
        self.last_sim_step = None
        self.last_sim_time = None
        self.prepared = None

    def require_neutral(self, required):
        if type(required) is not bool:
            raise ValueError("Protected-state requirement must be boolean")
        if required and self.neutral_check is None:
            raise RuntimeError("Cannot protect publication without verified neutral snapshot")
        self.protected = required

    def next_deadline_ns(self):
        return deadline_ns(self.epoch_ns, self.deadline_index, self.rate_hz)

    def _claim(self, now):
        claimed = claim_deadline(self.epoch_ns, self.deadline_index, self.rate_hz, now)
        if claimed is None:
            return None
        due_index, missed = claimed
        self.deadline_index = due_index+1
        self.missed += missed
        return due_index, missed

    def _latch(self, error):
        # Latch; only an explicit, independently verified reset/restart can resume.
        self.fault = str(error) if str(error) in {"NEUTRAL_DIVERGED", "SIMULATION_NOT_PROGRESSING"} else "PUBLISHER_FAILURE"
        self.prepared = None

    def _checked_sample(self, sim_time, sim_step):
        if self.last_sim_step is not None and sim_step <= self.last_sim_step:
            raise RuntimeError("SIMULATION_NOT_PROGRESSING")
        if self.last_sim_time is not None and sim_time <= self.last_sim_time:
            raise RuntimeError("SIMULATION_NOT_PROGRESSING")
        if self.timing is not None: self.timing.record('sample_begin', a=sim_step)
        positions, objects, complete = self.sample()
        observed_ns = self.clock_ns() if self.observation is not None else None
        if self.timing is not None: self.timing.record('sample_end', a=sim_step)
        valid = False
        if self.protected:
            if self.timing is not None: self.timing.record('neutral_begin', a=sim_step)
            checked = self.neutral_check(complete)
            if self.timing is not None: self.timing.record('neutral_end', a=sim_step)
            valid = checked is True or (isinstance(checked, dict) and checked.get("reset_ok") is True)
            if not valid:
                raise RuntimeError("NEUTRAL_DIVERGED")
        return positions, objects, complete, observed_ns, valid

    def _emit(self, frame, payload, complete, observed_ns, valid, sim_time, sim_step,
              serialize_ns, encoded_ns, missed, due_index):
        self.transport.submit(payload)
        submitted_ns = self.clock_ns()
        if self.observation is not None:
            try:
                self.observation.after_publish(frame,payload,complete,observed_ns,
                    protected=self.protected,neutral_valid=valid)
            except Exception:
                # Capture failure is not source/exposure authority and does
                # not weaken or replace the normal publisher checks.
                self.observation.fail('OBS_CALLBACK_FAILED')
        status = self.transport.metrics()
        stamp = int(frame["host_monotonic_ns"])
        self.log.offer(dict(seq=frame["seq"], host_monotonic_ns=frame["host_monotonic_ns"],
            sim_step=sim_step, sim_time=sim_time, serialize_ms=serialize_ns/1e6,
            handoff_ms=(submitted_ns-encoded_ns)/1e6, missed_deadlines=missed,
            connected_clients=status["connected_clients"], queue_overwrites=status["queue_overwrites"],
            deadline_lag_ms=(stamp-deadline_ns(self.epoch_ns, due_index, self.rate_hz))/1e6))
        self.last_publish_ns = stamp
        self.last_sim_step = sim_step
        self.last_sim_time = sim_time
        self.published += 1
        return frame

    def after_step(self, sim_time, sim_step):
        if self.closed or self.fault:
            return None
        now = self.clock_ns()
        claimed = self._claim(now)
        if claimed is None:
            return None
        due_index, missed = claimed
        # A frame prepared but not published is discarded unpublished; it never
        # consumed a sequence number.
        self.prepared = None
        try:
            positions, objects, complete, observed_ns, valid = self._checked_sample(sim_time, sim_step)
            if self.timing is not None: self.timing.record('encode_begin', a=sim_step)
            frame = self.encoder.build(positions, objects, sim_time, sim_step)
            payload = encode(frame)
            if self.timing is not None: self.timing.record('encode_end', a=sim_step)
            encoded_ns = self.clock_ns()
            return self._emit(frame, payload, complete, observed_ns, valid, sim_time, sim_step,
                              encoded_ns-now, encoded_ns, missed, due_index)
        except Exception as error:
            self._latch(error)
            return None

    def prepare(self, sim_time, sim_step):
        """Sample, check and encode the current state ahead of its deadline."""
        if self.closed or self.fault:
            return None
        self.prepared = None
        started = self.clock_ns()
        try:
            positions, objects, complete, observed_ns, valid = self._checked_sample(sim_time, sim_step)
            if self.timing is not None: self.timing.record('encode_begin', a=sim_step)
            encoded = self.encoder.prepare(positions, objects, sim_time, sim_step)
            if self.timing is not None: self.timing.record('encode_end', a=sim_step)
        except Exception as error:
            self._latch(error)
            return None
        self.prepared = _Prepared(encoded, sim_time, sim_step, complete, observed_ns, valid,
                                  self.clock_ns()-started)
        return self.prepared

    def publish_prepared(self, sim_time, sim_step):
        """Stamp and hand off the prepared frame once its deadline is due.

        Returns None without side effects while the deadline is not yet due.
        """
        if self.closed or self.fault:
            return None
        prepared = self.prepared
        if prepared is None or prepared.sim_step != sim_step or prepared.sim_time != sim_time:
            # The simulator advanced (or nothing was prepared): never publish
            # a state that is no longer current under a later host stamp.
            self._latch(RuntimeError("STALE_PREPARED_STATE"))
            return None
        now = self.clock_ns()
        claimed = self._claim(now)
        if claimed is None:
            return None
        due_index, missed = claimed
        self.prepared = None
        try:
            frame, payload = self.encoder.commit(prepared.encoded, now)
            return self._emit(frame, payload, prepared.complete, prepared.observed_ns, prepared.valid,
                              sim_time, sim_step, prepared.serialize_ns, now, missed, due_index)
        except Exception as error:
            self._latch(error)
            return None

    def health(self):
        now = self.clock_ns()
        age_ms = None if self.last_publish_ns is None else max(0, now-self.last_publish_ns)/1e6
        status = self.transport.metrics()
        return dict(version=1, kind="publisher_health", station_id=self.encoder.registry.station_id,
                    rate_hz=self.rate_hz, published=self.published, last_publish_host_ns=None if self.last_publish_ns is None else str(self.last_publish_ns),
                    age_ms=age_ms, stale=age_ms is None or age_ms > 250, missed_deadlines=self.missed,
                    fault=self.fault or ("TRANSPORT_FAILURE" if status.get("failed") else None),
                    **{key: status[key] for key in ("connected_clients", "queue_overwrites")})

    def close(self):
        self.closed = True
        self.prepared = None
        try:
            self.transport.close()
        finally:
            self.log.close()
