"""Bounded actual-neutral stream for the Unity diagnostic, never a study run."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import time

from isaac.reset.benchmark import durable
from .benchmark import registry_from_snapshot
from .runtime import StatePublisher
from .transport import WebSocketTransport


def run_protected_stream(reset_manager, layout, output, *, hold_robot, socket_path,
                         station_id="simulator-01", seconds=300, rate_hz=30, joint_csv=None):
    """Use the #55 robot-only hold callback after each actual physics step.

    The caller supplies a previously validated #53 ResetManager and a restricted
    Unix socket directory. No private command listener or session scheduling is
    exposed. The full actual scene is verified before every public frame.
    """
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 600:
        raise ValueError("Diagnostic duration must be finite and within 600 seconds")
    if type(rate_hz) is not int or rate_hz not in (30, 60) or not callable(hold_robot):
        raise ValueError("Explicit 30/60 Hz and robot-only hold callback required")
    adapter = reset_manager.adapter
    if not math.isclose(adapter.sim.get_physics_dt(), 1/60, abs_tol=1e-8, rel_tol=0):
        raise ValueError("Actual 60 Hz physics required")
    neutral = reset_manager.neutral_state
    binding = {"state": neutral, "scene_sha256": adapter.scene_sha256}
    joint_csv = joint_csv or Path(__file__).resolve().parents[2]/"docs/spikes/isaac/joint_inventory.csv"
    registry = registry_from_snapshot(layout, binding, reset_manager.reset_snapshot_sha256, station_id, joint_csv)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    transport = publisher = None
    first = last = None
    failure = None
    steps = 0
    started = ended = time.monotonic_ns()
    try:
        reset = reset_manager.reset()
        if reset.get("reset_ok") is not True:
            raise RuntimeError("INITIAL_RESET_FAILED")
        durable(output/"initial-reset.json", (json.dumps(reset, indent=2)+"\n").encode())
        transport = WebSocketTransport(socket_path=socket_path)
        os.chmod(socket_path, 0o600)

        def sample():
            state = adapter.read_state()
            if tuple(state["robot"]["joint_names"]) != registry.joint_names:
                raise RuntimeError("CANONICAL_ORDER_CHANGED")
            return state["robot"]["joint_positions_rad"], state["objects"], state

        publisher = StatePublisher(registry, sample, transport, output/"publish.csv", rate_hz=rate_hz,
                                   neutral_check=reset_manager.verify_state)
        publisher.require_neutral(True)
        transport.health_provider = publisher.health
        started = time.monotonic_ns()
        publisher.epoch_ns = started
        tick = 0
        durable(output/"ready.json", (json.dumps({"station_id": station_id, "scene_sha256": registry.scene_sha256,
                "reset_snapshot_sha256": registry.reset_snapshot_sha256, "requested_seconds": seconds,
                "qualification": "actual_neutral_diagnostic_only"}, indent=2)+"\n").encode())
        while time.monotonic_ns()-started < seconds*1e9:
            for _ in range(60//rate_hz):
                adapter.robot.write_data_to_sim()
                adapter.sim.step(render=False)
                adapter.robot.update(adapter.sim.get_physics_dt())
                hold_robot(neutral["robot"])
                steps += 1
            deadline = started + tick*1_000_000_000//rate_hz
            remaining = (deadline-time.monotonic_ns())/1e9
            if remaining > 0:
                time.sleep(remaining)
            frame = publisher.after_step(adapter.sim_time, steps)
            if frame is None:
                raise RuntimeError(publisher.fault or "PUBLISH_FAILED")
            if first is None:
                first = frame
            last = frame
            tick = max(tick+1, publisher.deadline_index)
        ended = time.monotonic_ns()
    except Exception as error:
        ended = time.monotonic_ns()
        failure = str(error) if isinstance(error, RuntimeError) and str(error) in {
            "INITIAL_RESET_FAILED", "CANONICAL_ORDER_CHANGED", "NEUTRAL_DIVERGED", "PUBLISH_FAILED", "PUBLISHER_FAILURE"
        } else type(error).__name__
    finally:
        try:
            if publisher:
                publisher.close()
            elif transport:
                transport.close()
        except Exception:
            failure = failure or "EVIDENCE_FINALIZATION_FAILED"
    for name, frame in (("sample-first.json", first), ("sample-last.json", last)):
        if frame is not None:
            durable(output/name, (json.dumps(frame, indent=2, allow_nan=False)+"\n").encode())
    report = dict(source_kind="live", protected_neutral_diagnostic=True, station_id=station_id,
                  scene_sha256=registry.scene_sha256, reset_snapshot_sha256=registry.reset_snapshot_sha256,
                  requested_seconds=seconds, elapsed_seconds=(ended-started)/1e9,
                  completed=failure is None and (ended-started)/1e9 >= seconds, fault=failure,
                  frames=publisher.published if publisher else 0, physics_steps=steps,
                  rate_hz=rate_hz, missed_deadlines=publisher.missed if publisher else 0,
                  limitations=["No study cues or private command endpoint", "Not a one-hour rate or headset qualification"])
    report["hashes"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()}
    durable(output/"summary.json", (json.dumps(report, indent=2, allow_nan=False)+"\n").encode())
    return report
