"""Bounded real-simulator perturb/reset evidence; run only in the isolated scene."""
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import time
import uuid

from .snapshot import capture, snapshot_bytes, sha256
from .manager import ResetManager
from .event_log import DurableResetLog


def durable(path, raw):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".pending")
    with temporary.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    if path.exists():
        raise FileExistsError(path)
    temporary.rename(path)


def run_reset_check(adapter, output, cycles=1000, capture_image=None):
    if type(cycles) is not int or cycles < 1:
        raise ValueError("positive cycle count required")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    adapter.prepare_neutral()
    snapshot = capture(adapter)
    durable(output/"neutral_v1.json", snapshot_bytes(snapshot))
    log = DurableResetLog(output/"reset-events.jsonl", session_id=uuid.uuid4().hex,
                          apparatus_version="workcell-development-v1", protocol_version="unresolved-methodology")
    manager = ResetManager(adapter, snapshot, sha256(snapshot), log)
    randomizer = random.Random(531000)  # Public engineering fixture seed only.
    rows = []
    limits = adapter.robot.data.joint_pos_limits[0].tolist()
    try:
        for cycle in range(cycles):
            changed = manager.neutral_state
            changed["robot"]["joint_positions_rad"] = [randomizer.uniform(lo, hi) for lo, hi in limits]
            changed["robot"]["joint_velocities_rad_s"] = [randomizer.uniform(-.1, .1) for _ in limits]
            for obj in changed["objects"].values():
                obj["position_m"] = [v + randomizer.uniform(-.01, .01) for v in obj["position_m"]]
                angle = randomizer.uniform(-math.pi, math.pi)
                obj["rotation_xyzw"] = [0., 0., math.sin(angle/2), math.cos(angle/2)]
                obj["linear_velocity_m_s"] = [randomizer.uniform(-.1, .1) for _ in range(3)]
                obj["angular_velocity_rad_s"] = [randomizer.uniform(-.1, .1) for _ in range(3)]
                obj["visible"] = not obj["visible"]
                obj["enabled"] = not obj["enabled"]
                for key in obj["state"]:
                    if key == "card_face": obj["state"][key] = 1 - obj["state"][key]
                    elif key == "arrow_angle_rad": obj["state"][key] += .3
                    elif key == "lid_open_fraction": obj["state"][key] = 1 - obj["state"][key]
                    elif key == "tag_attached": obj["state"][key] = not obj["state"][key]
            adapter.write_state(changed)
            adapter.step_fixed(1)
            started = time.perf_counter_ns()
            reply = manager.reset()
            elapsed_ms = (time.perf_counter_ns()-started)/1e6
            rows.append({"cycle": cycle, "reset_ok": reply["reset_ok"], "elapsed_ms_including_log": elapsed_ms,
                         "sim_time": reply["sim_time"], **reply["worst_deviation"]})
            if not reply["reset_ok"]:
                durable(output/"first-failure.json", (json.dumps(reply, indent=2)+"\n").encode())
                break  # No silent retry after a real fault.
        # Inject an actual wrong joint write in the adapter, after the normal
        # restore, to model an unreachable/locked actuator. Never production code.
        original_write = adapter.write_state
        def locked_write(value):
            value["robot"]["joint_positions_rad"][0] += .1
            original_write(value)
        adapter.write_state = locked_write
        fault = manager.reset()
        adapter.write_state = original_write
        recovery = manager.reset()  # Explicit recovery, logged as a new attempt.
        if capture_image and recovery["reset_ok"]:
            capture_image(output/"neutral-observer.png")
    finally:
        log.close()
    fields = list(rows[0])
    with (output/"reset-cycles.csv").open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())
    times = sorted(row["elapsed_ms_including_log"] for row in rows)
    report = {"source": "loaded_isaac_articulation_and_workcell", "cycles_requested": cycles,
              "cycles_completed": len(rows), "all_cycles_passed": len(rows) == cycles and all(row["reset_ok"] for row in rows),
              "reset_snapshot_sha256": sha256(snapshot), "scene_sha256": adapter.scene_sha256,
              "joint_count": len(snapshot["state"]["robot"]["joint_names"]), "object_count": len(snapshot["state"]["objects"]),
              "forward_frames_per_reset": snapshot["fixed_steps"], "physics_integration_during_reset": False,
              "elapsed_ms_including_log": {"median": statistics.median(times), "p95": times[math.ceil(.95*len(times))-1], "max": max(times)},
              "locked_joint_injection_rejected": not fault["reset_ok"], "explicit_recovery_ok": recovery["reset_ok"],
              "methodology_review_complete": False, "gaze_neutrality_review_complete": False,
              "hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir()) if p.is_file()}}
    durable(output/"summary.json", (json.dumps(report, indent=2, allow_nan=False)+"\n").encode())
    return report
