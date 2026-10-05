"""Simulation-thread command state machine with a single protected lock.

Demo hooks are iterators. Each yield is an interruptible safe point; completion
returns {execution_ok: bool}. Missing motion content is an explicit rejection.
"""
from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
import threading
import time
import uuid
import re

from .protocol import decode, validate, target_bearing, fingerprint


class CommandDispatcher:
    def __init__(self, reset_manager, event_sink, *, station_id, allowed_client,
                 demo_factory=None, hold_robot=None, publisher=None, cache_size=1024):
        if not isinstance(station_id, str) or not station_id or not isinstance(allowed_client, str) or not allowed_client:
            raise ValueError("explicit station and client identities required")
        if type(cache_size) is not int or cache_size < 1:
            raise ValueError("positive idempotency cache size required")
        self.reset_manager, self.sink = reset_manager, event_sink
        self.station_id, self.allowed_client = station_id, allowed_client
        self.demo_factory, self.hold_robot, self.publisher = demo_factory, hold_robot, publisher
        self.mode = "test"
        self.control_session_id = uuid.uuid4().hex
        self.paused = self.stopped = False
        self.neutral_hold = True
        self.paused_robot = None
        self.fault = None
        self.active = None
        self.cache, self.cache_size = OrderedDict(), cache_size
        self.owner = threading.get_ident()
        if publisher:
            publisher.require_neutral(True)

    def _thread(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError("commands must dispatch on the simulation thread")

    def health(self):
        self._thread()
        return {"control_session_id": self.control_session_id, "mode": self.mode, "paused": self.paused, "stopped": self.stopped,
                "fault": self.fault, "demo_active": self.active is not None,
                "exposure_ready": self.mode == "test" and self.neutral_hold and self.reset_manager.exposure_ready and not self.fault and not self.paused and not self.stopped,
                "public_stream_recovered": False}

    def _complete(self, job, accepted, reason, *, reset_ok=None, duplicate=False, remember=True):
        value = job["value"]
        request_id = value.get("request_id") if isinstance(value, dict) else None
        if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id):
            request_id = None
        reply = {"version": 1, "kind": "private_reply", "request_id": request_id,
                 "accepted": bool(accepted), "reason": reason, "mode": self.mode,
                 "host_mono_ms": time.monotonic_ns()/1e6, "sim_time": float(self.reset_manager.adapter.sim_time),
                 "reset_ok": reset_ok, "duplicate": duplicate, "health": self.health()}
        event = {"station_id": self.station_id, "mode": self.mode, "client": job["peer"],
                 "raw_command": job["raw"], "command": value.get("command") if isinstance(value, dict) else None,
                 "arguments": value.get("args") if isinstance(value, dict) else None, "reply": deepcopy(reply)}
        try:
            self.sink(event)  # Must be durable before an acknowledgement escapes.
        except Exception as exc:
            self.fault = "COMMAND_LOG_FAILED"
            if not job["future"].done():
                job["future"].set_exception(exc)
            raise
        if remember and job.get("fingerprint"):
            self.cache[value["request_id"]] = (job["fingerprint"], deepcopy(reply))
            self.cache.move_to_end(value["request_id"])
        if not job["future"].done():
            job["future"].set_result(reply)
        return reply

    def submit(self, raw, peer):
        self._thread()
        try:
            return self._submit(raw, peer)
        except Exception as error:
            if self.fault == "COMMAND_LOG_FAILED":
                failed = Future()
                failed.set_exception(error)
                return failed
            self.fault = "COMMAND_DISPATCH_FAILED"
            try:
                value = decode(raw)
            except (ValueError, TypeError):
                value = None
            job = dict(raw=raw, peer=peer, value=value, future=Future())
            self._complete(job, False, "COMMAND_FAILED", remember=False)
            return job["future"]

    def _submit(self, raw, peer):
        self._thread()
        job = {"raw": raw, "peer": peer, "value": None, "future": Future()}
        try:
            job["value"] = decode(raw)
        except (ValueError, TypeError):
            self._complete(job, False, "MALFORMED", remember=False)
            return job["future"]
        if peer != self.allowed_client:
            self._complete(job, False, "UNKNOWN_CLIENT", remember=False)
            return job["future"]
        # This check deliberately precedes schema dispatch AND replay lookup.
        # Replaying an old accepted teaching demo in test mode is still denied.
        if self.mode == "test" and target_bearing(job["value"]):
            self._complete(job, False, "PROTECTED_TARGET_COMMAND", remember=False)
            return job["future"]
        try:
            value = validate(job["value"])
        except (ValueError, TypeError):
            self._complete(job, False, "MALFORMED", remember=False)
            return job["future"]
        job["fingerprint"] = fingerprint(value)
        if value["control_session_id"] != self.control_session_id:
            self._complete(job, False, "CONTROL_SESSION_MISMATCH", remember=False)
            return job["future"]
        cached = self.cache.get(value["request_id"])
        if cached:
            if cached[0] != job["fingerprint"]:
                self._complete(job, False, "REQUEST_ID_CONFLICT", remember=False)
            else:
                prior = cached[1]
                # Re-evaluate current health/mode; never imply that an old reset
                # acknowledgement proves a newly changed scene is neutral.
                self._complete(job, prior["accepted"], prior["reason"], reset_ok=prior["reset_ok"], duplicate=True, remember=False)
            return job["future"]
        if self.active and value["request_id"] == self.active["job"]["value"]["request_id"]:
            self._complete(job, False, "REQUEST_IN_PROGRESS" if job["fingerprint"] == self.active["job"]["fingerprint"] else "REQUEST_ID_CONFLICT", remember=False)
            return job["future"]
        if len(self.cache) >= self.cache_size:
            # Never evict a request and accidentally execute an old retry again.
            self.fault, self.paused = "IDEMPOTENCY_CAPACITY", True
            self.neutral_hold = True
            self._interrupt_demo("CAPACITY_INTERRUPTED")
            self._complete(job, False, "IDEMPOTENCY_CAPACITY", remember=False)
            return job["future"]
        command, args = value["command"], value["args"]
        if self.fault and command not in ("stop", "health", "reset", "hold_neutral"):
            self._complete(job, False, "FAULT_LATCHED")
        elif command == "demo":
            if self.paused or self.stopped or self.active:
                self._complete(job, False, "NOT_READY")
            elif self.demo_factory is None:
                self._complete(job, False, "DEMO_NOT_IMPLEMENTED")
            elif not self.reset_manager.exposure_ready or not self.reset_manager.verify_current()["reset_ok"]:
                self._complete(job, False, "RESET_REQUIRED")
            else:
                try:
                    iterator = iter(self.demo_factory(args["action"], args["target"]))
                    self.active = {"job": job, "iterator": iterator}
                    self.neutral_hold = False
                except Exception:
                    self._complete(job, False, "DEMO_START_FAILED")
        elif command == "set_mode":
            if args["mode"] == "test":
                self.mode = "test"  # Lock first, before interrupt/reset/callbacks.
                if self.publisher:
                    self.publisher.require_neutral(True)
                self._interrupt_demo("PROTECTED_MODE_INTERRUPTED")
                result = self.reset_manager.reset()
                self.neutral_hold = True
                self._complete(job, result["reset_ok"], "MODE_CHANGED" if result["reset_ok"] else "RESET_FAILED", reset_ok=result["reset_ok"])
            elif self.active:
                self._complete(job, False, "DEMO_ACTIVE")
            else:
                self.mode = args["mode"]
                if self.publisher:
                    self.publisher.require_neutral(False)
                self._complete(job, True, "MODE_CHANGED")
        elif command in ("reset", "hold_neutral"):
            self._interrupt_demo("RESET_INTERRUPTED")
            result = self.reset_manager.reset()
            self.neutral_hold = True
            self._complete(job, result["reset_ok"], "RESET_COMPLETE" if result["reset_ok"] else "RESET_FAILED", reset_ok=result["reset_ok"])
        elif command == "pause":
            self.paused = True
            self.paused_robot = deepcopy(self.reset_manager.adapter.read_state()["robot"])
            self._complete(job, True, "PAUSED_AT_SAFE_POINT")
        elif command == "resume":
            if self.stopped:
                self._complete(job, False, "STOPPED_RESTART_REQUIRED")
            else:
                self.paused = False
                self._complete(job, True, "RESUMED_EXPLICITLY")
        elif command == "stop":
            self.stopped = self.paused = True
            self._interrupt_demo("STOP_INTERRUPTED")
            result = self.reset_manager.reset()
            self._complete(job, result["reset_ok"], "STOPPED" if result["reset_ok"] else "STOP_RESET_FAILED", reset_ok=result["reset_ok"])
        else:
            self._complete(job, True, "HEALTH")
        return job["future"]

    def _interrupt_demo(self, reason):
        if not self.active:
            return
        active, self.active = self.active, None
        try:
            close = getattr(active["iterator"], "close", None)
            if close:
                close()
        except Exception:
            self.fault = "DEMO_CANCEL_FAILED"
        finally:
            self._complete(active["job"], False, reason)

    def advance(self):
        """One safe demo point per simulator step; no hidden loop or sleep."""
        self._thread()
        if self.active and not (self.paused or self.stopped or self.fault):
            if self.mode == "test":
                self._interrupt_demo("PROTECTED_MODE_INTERRUPTED")
                return
            active = self.active
            try:
                next(active["iterator"])
            except StopIteration as complete:
                self.active = None
                result = complete.value
                okay = isinstance(result, dict) and result.get("execution_ok") is True
                self._complete(active["job"], okay, "DEMO_COMPLETE" if okay else "EXECUTION_FAILED")
            except Exception:
                self.active = None
                self._complete(active["job"], False, "EXECUTION_FAILED")

    def after_physics_step(self):
        """Explicit robot-only kinematic hold; object/appearance drift is a fault."""
        self._thread()
        if self.mode == "test" or self.stopped or self.neutral_hold or self.paused:
            if self.hold_robot is None:
                self.fault = "HOLD_NOT_CONFIGURED"
                return False
            wanted = self.paused_robot if self.paused and not self.neutral_hold and self.mode != "test" and not self.stopped else self.reset_manager.neutral_state["robot"]
            try:
                self.hold_robot(wanted)
                if self.mode == "test" or self.stopped or self.neutral_hold:
                    result = self.reset_manager.verify_current()
                    if not result["reset_ok"]:
                        self.fault = "NEUTRAL_DIVERGED"
                        return False
            except Exception:
                self.fault = "HOLD_FAILED"
                return False
        return self.fault is None
