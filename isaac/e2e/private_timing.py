"""Opt-in, bounded observer for private transport/source latency.

No callback serializes, writes, waits for a lock, or changes GC settings. Rows
contain only identifiers, timestamps and numeric metadata, never payloads.
The record lock is deliberately nonblocking, including from gc.callbacks;
contention/reentrancy loses evidence and makes the trace explicitly incomplete.
"""
from __future__ import annotations

import asyncio
import gc
import json
import math
import os
from pathlib import Path
import threading
import time


class PrivateTiming:
    FIELDS = ('host_ns', 'kind', 'thread_id', 'request_id', 'connection', 'a', 'b', 'c')

    def __init__(self, path, seconds, *, capacity=120000, clock_ns=time.monotonic_ns):
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 1 <= seconds <= 900:
            raise ValueError('Trace duration must be explicit and bounded1..900s')
        if type(capacity) is not int or not 1 <= capacity <= 200000:
            raise ValueError('Trace capacity must be bounded1..200000')
        self.path = Path(path)
        if self.path.exists():
            raise FileExistsError('Do not replace timing evidence')
        self.seconds, self.clock_ns = seconds, clock_ns
        self.rows = [None] * capacity
        self.lock = threading.Lock()
        self.count = self.dropped = self.errors = 0
        self.start_ns = self.end_ns = None
        self.closed = False
        self.callback = self._gc_callback
        self.callback_registered = False
        self.gc_before = None

    def start(self):
        if self.start_ns is not None or self.closed:
            raise RuntimeError('Trace may only be started once')
        self.gc_before = (gc.isenabled(), gc.get_threshold())
        self.start_ns = self.clock_ns()
        self.end_ns = self.start_ns + int(self.seconds * 1e9)
        gc.callbacks.append(self.callback)
        self.callback_registered = True

    @property
    def finished(self):
        return self.closed or (self.end_ns is not None and self.clock_ns() >= self.end_ns)

    def record(self, kind, request_id='', connection=0, a=0, b=0, c=0):
        # Includes the callback's timestamp/counter cost in the observed process.
        # Nonblocking acquire also handles synchronous GC reentry during append.
        if self.closed or self.start_ns is None:
            return
        stamp = self.clock_ns()
        if stamp >= self.end_ns:
            return
        if not self.lock.acquire(False):
            self.dropped += 1
            return
        try:
            if self.closed:
                return
            if self.count == len(self.rows):
                self.dropped += 1
                return
            self.rows[self.count] = (stamp, kind, threading.get_ident(), request_id,
                                     connection, a, b, c)
            self.count += 1
        except Exception:
            self.errors += 1
        finally:
            self.lock.release()

    def _gc_callback(self, phase, info):
        # Primitive tuples only; start/stop pairing is computed offline by
        # generation/thread. Never call GC, format strings or copy its graph.
        self.record('gc_start' if phase == 'start' else 'gc_stop',
                    a=info.get('generation', -1), b=info.get('collected', 0),
                    c=info.get('uncollectable', 0))

    async def loop_watch(self):
        while not self.finished:
            expected = self.clock_ns() + 20_000_000
            await asyncio.sleep(.02)
            self.record('loop_lag', a=max(0, self.clock_ns() - expected))

    def close(self):
        if self.closed:
            return
        # Called only after transport/source observers are stopped. No trace
        # output fsync is inserted into a live physics or health exchange.
        self.closed = True
        if self.callback_registered:
            try:
                gc.callbacks.remove(self.callback)
            except ValueError:
                self.errors += 1
            self.callback_registered = False
        stopped = self.clock_ns()
        with self.lock:
            rows = self.rows[:self.count]
        gc_after = (gc.isenabled(), gc.get_threshold())
        report = dict(version=1, scope='bounded diagnostic observer', qualification=False,
            clock='Python time.monotonic_ns on source host', fields=self.FIELDS,
            requested_seconds=self.seconds, start_host_ns=self.start_ns,
            requested_end_host_ns=self.end_ns, closed_host_ns=stopped,
            capacity=len(self.rows), events=self.count, dropped=self.dropped, errors=self.errors,
            gc_before=self.gc_before, gc_after=gc_after,
            complete=self.end_ns is not None and stopped >= self.end_ns and
                     self.dropped == 0 and self.errors == 0 and self.gc_before == gc_after,
            observer_cost='Every enabled hook reads a clock and attempts a nonblocking lock; '
                          'accepted events allocate one bounded primitive tuple. No callback I/O. '
                          'This run is instrumented, not a throughput qualification.', rows=rows)
        with self.path.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, separators=(',', ':'), allow_nan=False)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
