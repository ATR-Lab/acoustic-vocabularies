"""Experimental single-iteration publication proof, never a cross-step cache.

The ordinary publisher and E2E service do not enable this module. A candidate
runner must bind its sample/check callbacks explicitly and retain the existing
physics, hold, complete readback and neutral comparator. Immutable JSON bytes
hold the complete verified state. All values are reacquired next iteration.
"""
from dataclasses import dataclass
import json
import math
import threading
import time


def _bytes(value):
    # Keep insertion order: the existing encoder preserves visual-state key
    # order, so sorting here would change otherwise equivalent wire bytes.
    return json.dumps(value, separators=(",", ":"),
                      allow_nan=False, ensure_ascii=True).encode("utf-8")


@dataclass(frozen=True)
class _Capture:
    generation: int
    step: int
    sim_time: float
    scene_sha256: str
    snapshot_sha256: str
    started_ns: int
    identity: tuple
    state_bytes: bytes


class SameIterationCapture:
    """Service-local, owner-thread, single-use full-neutral verification proof.

identity() must inspect the real stage/layer binding; notify_mutation() must be
registered for USD notices. Owner-thread control/write/physics boundaries call
invalidate() explicitly. No token is returned to a command or network client.
"""
    def __init__(self, manager, identity, *, clock_ns=time.monotonic_ns,
                 max_age_ns=250_000_000):
        if not callable(identity) or type(max_age_ns) is not int or not 0 < max_age_ns <= 250_000_000:
            raise ValueError("Explicit identity reader and bounded capture age required")
        self._manager, self.identity, self.clock_ns = manager, identity, clock_ns
        self.max_age_ns = max_age_ns
        self.owner = threading.get_ident()
        self._generation = 0
        self._step = -1
        self._sim_time = None
        self._capture = self._reading = self._sampled = None
        self._consumed = False
        self._closed = False
        self._lock = threading.RLock()

    @property
    def manager(self):
        return self._manager

    def _owner(self):
        if self._closed or threading.get_ident() != self.owner:
            raise RuntimeError("Capture inactive or wrong owner thread")

    def notify_mutation(self, *_):
        # USD may deliver a notice on another thread. It can only invalidate.
        with self._lock:
            self._generation += 1
            self._capture = self._sampled = None

    def invalidate(self, reason):
        self._owner()
        if not isinstance(reason, str) or not reason:
            raise ValueError("Explicit invalidation reason required")
        self.notify_mutation()
        self._reading = None

    def bind_iteration(self, step, sim_time):
        self._owner()
        if (type(step) is not int or step <= self._step or type(sim_time) not in (int, float)
                or not math.isfinite(sim_time) or sim_time < 0
                or self._sim_time is not None and sim_time <= self._sim_time):
            raise ValueError("Actual progressing iteration required")
        self.invalidate("next iteration")
        self._step, self._sim_time = step, sim_time

    def before_read(self):
        self._owner()
        if self._step < 0 or self._reading is not None or self._capture is not None:
            raise RuntimeError("One full read per bound iteration required")
        identity = self.identity()
        with self._lock:
            self._reading = (self._generation, self.clock_ns(), identity)

    def after_read(self, state, result):
        self._owner()
        if self._reading is None:
            raise RuntimeError("Full read was not started")
        generation, started, identity = self._reading
        self._reading = None
        if not isinstance(result, dict) or result.get("reset_ok") is not True:
            self.invalidate("neutral check failed")
            return
        body = _bytes(state)  # Fully detached immutable authority, not a shared dict.
        capture = _Capture(generation, self._step, self._sim_time,
            self.manager.adapter.scene_sha256, self.manager.reset_snapshot_sha256,
            started, identity, body)
        with self._lock:
            self._capture = capture
            self._sampled = None
            self._consumed = False
        self._check(capture)

    def _check(self, expected=None):
        self._owner()
        try:
            current_identity = self.identity()
        except Exception:
            with self._lock:
                self._capture = self._sampled = None
            raise
        with self._lock:
            value = self._capture
            age = self.clock_ns() - value.started_ns if value is not None else -1
            if (value is None or expected is not None and value is not expected or self._consumed
                    or value.generation != self._generation or value.step != self._step
                    or value.sim_time != self._sim_time or value.sim_time != self.manager.adapter.sim_time
                    or value.scene_sha256 != self.manager.adapter.scene_sha256
                    or value.snapshot_sha256 != self.manager.reset_snapshot_sha256
                    or value.identity != current_identity or not 0 <= age <= self.max_age_ns):
                self._capture = self._sampled = None
                raise RuntimeError("Verified capture invalid, stale or already consumed")
            return value

    def sample(self):
        value = self._check()
        with self._lock:
            if self._sampled is not None:
                raise RuntimeError("Verified capture already sampled")
            state = json.loads(value.state_bytes)
            self._sampled = state
        self._check(value)
        return state["robot"]["joint_positions_rad"], state["objects"], state

    def neutral_check(self, state):
        value = self._check()
        # The generic publisher's normal guard still runs. This branch proves
        # that its exact complete sample already passed the same full comparator.
        if state is not self._sampled or _bytes(state) != value.state_bytes:
            self.invalidate("sample changed after verification")
            raise RuntimeError("Complete verified sample identity/content changed")
        self._check(value)
        return True

    def _validated_payload(self, payload):
        value = self._check()
        if self._sampled is None or _bytes(self._sampled) != value.state_bytes:
            raise RuntimeError("Sample changed or absent at transport boundary")
        frame = json.loads(payload)
        state = self._sampled
        objects = [{"id": identifier, **{key: item[key] for key in
            ("position_m", "rotation_xyzw", "visible", "enabled", "state")}}
            for identifier, item in sorted(state["objects"].items())]
        if (frame["sim_step"] != value.step or frame["sim_time"] != value.sim_time
                or frame["scene_sha256"] != value.scene_sha256
                or frame["reset_snapshot_sha256"] != value.snapshot_sha256
                or frame["joint_names"] != state["robot"]["joint_names"]
                or frame["joint_positions"] != state["robot"]["joint_positions_rad"]
                or frame["objects"] != objects):
            raise RuntimeError("Encoded frame is not bound to the verified iteration")
        self._check(value)
        return value

    def submit_verified(self, payload, send):
        """Atomically check/consume/enqueue relative to notice invalidations.

send must be the existing bounded queue insertion, never simulator or user
code. This lock orders notifications; it cannot lock arbitrary USD authors.
The candidate therefore still requires exclusive serial scene authoring.
"""
        if not callable(send):
            raise TypeError("Bounded transport queue insertion required")
        with self._lock:
            value = self._validated_payload(payload)
            self._check(value)
            self._consumed = True
            self._sampled = None
            send(payload)

    def consume_payload(self, payload):
        """Consume without delivery, useful for isolated proof regression tests."""
        self.submit_verified(payload, lambda _: None)

    def close(self):
        self._owner()
        self.invalidate("close")
        self._closed = True


class VerifiedTransport:
    """Candidate-only adapter; the normal transport/publisher path is unchanged."""
    def __init__(self, transport, capture):
        self.transport, self.capture = transport, capture

    def submit(self, payload):
        self.capture.submit_verified(payload, self.transport.submit)

    def metrics(self):
        return self.transport.metrics()

    def close(self):
        self.transport.close()


class StageMutationWatch:
    """Conservative whole-stage notice and identity binding; no values cached."""
    def __init__(self, adapter, invalidate):
        from pxr import Sdf, Tf, Usd
        self.adapter, self.stage = adapter, adapter.accessors.stage
        self.invalidate = invalidate
        self._closed = False
        self._notices = []
        try:
            for name in ("ObjectsChanged", "StageContentsChanged", "StageEditTargetChanged", "LayerMutingChanged"):
                self._notices.append(Tf.Notice.Register(getattr(Usd.Notice, name), invalidate, self.stage))
            # Global Sdf changes are conservative: even an unrelated change
            # invalidates an in-flight proof. Missing bindings refuse opt-in.
            for name in ("LayersDidChange", "LayerDidReplaceContent", "LayerDidReloadContent",
                         "LayerIdentifierDidChange", "LayerInfoDidChange", "LayerMutenessChanged"):
                self._notices.append(Tf.Notice.RegisterGlobally(getattr(Sdf.Notice, name), invalidate))
        except Exception:
            self.close()
            raise

    def identity(self):
        if self._closed or self.stage is not self.adapter.accessors.stage:
            raise RuntimeError("Stage identity changed")
        return (self.stage, self.stage.GetRootLayer(), self.stage.GetSessionLayer(),
            tuple(self.stage.GetLayerStack(True)), tuple(sorted(self.stage.GetMutedLayers())),
            self.stage.GetEditTarget(), tuple(sorted(
                (layer.identifier, layer) for layer in self.stage.GetUsedLayers())))

    def close(self):
        self._closed = True
        self.invalidate()
        for notice in self._notices:
            notice.Revoke()
        self._notices.clear()


def advance_once_verified(adapter, dispatcher, handoff, publisher, capture, sim_step, trace=None):
    """Candidate only: serial owner, neutral hold, no demo authors or callbacks.

The caller owns all simulation/control authoring on this thread. Notices are
invalidation evidence, not an atomic lock against arbitrary external USD
authors. A concurrent-author environment is unsupported and must not opt in.
"""
    def require_serial_neutral():
        if (capture.manager is not dispatcher.reset_manager or capture.manager.adapter is not adapter
                or dispatcher.demo_factory is not None or dispatcher.active is not None
                or dispatcher.neutral_hold is not True):
            raise RuntimeError("Candidate requires its bound serial neutral-hold dispatcher")
    require_serial_neutral()
    capture.invalidate("before control boundary")
    handoff.drain()
    if dispatcher.fault:
        raise RuntimeError("PRIVATE_DISPATCHER_FAULT")
    if dispatcher.stopped:
        return sim_step, None, True
    require_serial_neutral()
    capture.invalidate("before target write and physics")
    adapter.robot.write_data_to_sim()
    if trace is None:
        adapter.sim.step(render=False)
    else:
        with trace.measure():
            adapter.sim.step(render=False)
    adapter.robot.update(adapter.sim.get_physics_dt())
    sim_step += 1
    capture.bind_iteration(sim_step, adapter.sim_time)
    try:
        # The hold precedes before_read(), so its writes cannot bless stale data.
        if not dispatcher.after_physics_step(capture=capture):
            raise RuntimeError("NEUTRAL_HOLD_FAULT")
        require_serial_neutral()
        frame = publisher.after_step(adapter.sim_time, sim_step)
        if publisher.fault:
            raise RuntimeError("PUBLIC_PUBLISHER_FAULT")
    finally:
        capture.invalidate("publication ended; before next control/write")
    handoff.drain()
    if dispatcher.fault:
        raise RuntimeError("PRIVATE_DISPATCHER_FAULT")
    handoff.refresh_health()
    return sim_step, frame, dispatcher.stopped
