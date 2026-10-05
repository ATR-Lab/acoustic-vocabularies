"""Bounded transport handoff; only drain() touches the command dispatcher."""
from concurrent.futures import Future
import itertools
import queue
import threading
import time
from copy import deepcopy

from .protocol import decode


class CommandQueue:
    def __init__(self, dispatcher, capacity=32):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("positive bounded command capacity required")
        self.dispatcher = dispatcher
        self.queue = queue.PriorityQueue(maxsize=capacity)
        self.sequence = itertools.count()
        self.lock = threading.Lock()
        self.closed = False
        self.refresh_health()

    def refresh_health(self):
        self.dispatcher._thread()
        with self.lock:
            self.cached_health = self.dispatcher.health()
            self.cached_sim_time = float(self.dispatcher.reset_manager.adapter.sim_time)

    def health(self):
        with self.lock:
            return deepcopy(self.cached_health)

    def deny(self, raw, peer, reason):
        # No simulator access here. Production sink is thread-safe and durable.
        with self.lock:
            health, sim_time = deepcopy(self.cached_health), self.cached_sim_time
        reply = dict(version=1, kind="private_reply", request_id=None, accepted=False, reason=reason,
                     mode=health["mode"], host_mono_ms=time.monotonic_ns()/1e6, sim_time=sim_time,
                     reset_ok=None, duplicate=False, health=health)
        self.dispatcher.sink(dict(station_id=self.dispatcher.station_id, mode=health["mode"], client=peer,
                                  raw_command=raw, command=None, arguments=None, reply=reply))
        return reply

    def submit(self, raw, peer):
        priority = 2
        try:
            value = decode(raw)
            if isinstance(value, dict) and (value.get("command") == "stop" or
                    value.get("command") == "set_mode" and value.get("args") == {"mode": "test"}):
                priority = 0
            elif isinstance(value, dict) and value.get("command") == "pause":
                priority = 1
        except (ValueError, TypeError):
            pass
        future = Future()
        reason = None
        with self.lock:
            if self.closed:
                reason = "SERVICE_STOPPING"
            else:
                try:
                    self.queue.put_nowait((priority, next(self.sequence), raw, peer, future))
                except queue.Full:
                    reason = "COMMAND_QUEUE_FULL"
        if reason:
            future.set_result(self.deny(raw, peer, reason))
        return future

    def drain(self, limit=8):
        self.dispatcher._thread()
        for _ in range(limit):
            try:
                _, _, raw, peer, output = self.queue.get_nowait()
            except queue.Empty:
                break
            try:
                pending = self.dispatcher.submit(raw, peer)
                def finish(result, output=output):
                    if output.done():
                        return
                    try:
                        output.set_result(result.result())
                    except Exception as exc:
                        output.set_exception(exc)
                pending.add_done_callback(finish)
            except Exception as exc:
                output.set_exception(exc)
        self.dispatcher.advance()
        self.refresh_health()

    def close(self):
        self.dispatcher._thread()
        with self.lock:
            self.closed = True
        self.dispatcher.stopped = self.dispatcher.paused = True
        self.dispatcher._interrupt_demo("SERVICE_STOPPING")
        self.refresh_health()
        while True:
            try:
                _, _, raw, peer, future = self.queue.get_nowait()
            except queue.Empty:
                break
            reply = self.deny(raw, peer, "SERVICE_STOPPING")
            if not future.done():
                future.set_result(reply)
