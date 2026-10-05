"""Simulation-thread scheduler. Transport and disk workers never step Isaac."""
from __future__ import annotations

import csv
import os
import queue
import threading
import time

from .protocol import StateEncoder, encode


class PublishLog:
    FIELDS = ("seq", "host_monotonic_ns", "sim_step", "sim_time", "serialize_ms",
              "handoff_ms", "missed_deadlines", "connected_clients", "queue_overwrites")

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


class StatePublisher:
    """Call after_step after Isaac's articulation/object readback, on its thread.

    sample() returns (canonical_positions, object_records, complete_reset_state).
    neutral_check(complete_reset_state) returns bool or a reset_ok reply without
    writing or stepping. Only a literal True qualifies.
    A missed deadline is logged and skipped; no catch-up/fabricated frames.
    """
    def __init__(self, registry, sample, transport, log_path, *, rate_hz=30,
                 neutral_check=None, source_kind="live", clock_ns=time.monotonic_ns):
        if type(rate_hz) is not int or rate_hz not in (30, 60):
            raise ValueError("Engineering rates are 30 or 60 Hz; final selection remains provisional")
        self.encoder = StateEncoder(registry, source_kind, clock_ns)
        self.sample, self.transport, self.clock_ns = sample, transport, clock_ns
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

    def require_neutral(self, required):
        if type(required) is not bool:
            raise ValueError("Protected-state requirement must be boolean")
        if required and self.neutral_check is None:
            raise RuntimeError("Cannot protect publication without verified neutral snapshot")
        self.protected = required

    def after_step(self, sim_time, sim_step):
        if self.closed or self.fault:
            return None
        now = self.clock_ns()
        if now < self.epoch_ns + self.deadline_index * 1_000_000_000 // self.rate_hz:
            return None
        due_index = max(self.deadline_index, (now-self.epoch_ns)*self.rate_hz//1_000_000_000)
        missed = due_index-self.deadline_index
        self.deadline_index = due_index+1
        self.missed += missed
        try:
            if self.last_sim_step is not None and sim_step <= self.last_sim_step:
                raise RuntimeError("SIMULATION_NOT_PROGRESSING")
            if self.last_sim_time is not None and sim_time <= self.last_sim_time:
                raise RuntimeError("SIMULATION_NOT_PROGRESSING")
            positions, objects, complete = self.sample()
            if self.protected:
                checked = self.neutral_check(complete)
                valid = checked is True or (isinstance(checked, dict) and checked.get("reset_ok") is True)
                if not valid:
                    raise RuntimeError("NEUTRAL_DIVERGED")
            frame = self.encoder.build(positions, objects, sim_time, sim_step)
            payload = encode(frame)
            encoded_ns = self.clock_ns()
            self.transport.submit(payload)
            submitted_ns = self.clock_ns()
            status = self.transport.metrics()
            self.log.offer(dict(seq=frame["seq"], host_monotonic_ns=frame["host_monotonic_ns"],
                sim_step=sim_step, sim_time=sim_time, serialize_ms=(encoded_ns-now)/1e6,
                handoff_ms=(submitted_ns-encoded_ns)/1e6, missed_deadlines=missed,
                connected_clients=status["connected_clients"], queue_overwrites=status["queue_overwrites"]))
            self.last_publish_ns = int(frame["host_monotonic_ns"])
            self.last_sim_step = sim_step
            self.last_sim_time = sim_time
            self.published += 1
            return frame
        except Exception as error:
            # Latch; only an explicit, independently verified reset/restart can resume.
            self.fault = str(error) if str(error) in {"NEUTRAL_DIVERGED", "SIMULATION_NOT_PROGRESSING"} else "PUBLISHER_FAILURE"
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
        try:
            self.transport.close()
        finally:
            self.log.close()
