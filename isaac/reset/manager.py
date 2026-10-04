"""One direct restore, fixed stepping, complete readback, and a latched gate."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, asdict
import math
import time

from .snapshot import sha256, validate_snapshot, validate_state


@dataclass(frozen=True)
class Tolerances:
    joint_rad: float = math.radians(.5)
    position_m: float = .001
    orientation_rad: float = math.radians(.5)
    # Explicit engineering proposals; not frozen protocol/pilot thresholds.
    linear_velocity_m_s: float = 1e-5
    angular_velocity_rad_s: float = 1e-5
    environment_absolute: float = 1e-7

    def __post_init__(self):
        if any(not math.isfinite(v) or v < 0 for v in asdict(self).values()):
            raise ValueError("finite nonnegative tolerances required")


def angle(a, b):
    # q and -q are the same orientation. atan2 remains useful near zero.
    dot = min(1., abs(sum(x*y for x, y in zip(a, b))))
    return 2 * math.atan2(math.sqrt(max(0., 1-dot*dot)), dot)


class ResetManager:
    def __init__(self, adapter, snapshot, expected_sha256, event_sink, tolerances=None):
        validate_snapshot(snapshot)
        if sha256(snapshot) != expected_sha256:
            raise ValueError("snapshot canonical SHA-256 mismatch")
        if adapter.scene_sha256 != snapshot["scene_sha256"]:
            raise ValueError("scene fingerprint mismatch")
        if not callable(event_sink):
            raise ValueError("durable reset event sink required")
        self.adapter = adapter
        self._snapshot = deepcopy(snapshot)
        self.reset_snapshot_sha256 = expected_sha256
        self.event_sink = event_sink
        self.tolerances = tolerances or Tolerances()
        self.exposure_ready = False

    @property
    def neutral_state(self):
        return deepcopy(self._snapshot["state"])

    def verify_state(self, state):
        failures = []
        worst = {"joint_rad": 0., "position_m": 0., "orientation_rad": 0.,
                 "linear_velocity_m_s": 0., "angular_velocity_rad_s": 0., "environment_absolute": 0.}
        try:
            validate_state(state)
            if self.adapter.scene_sha256 != self._snapshot["scene_sha256"]:
                raise ValueError("scene fingerprint changed")
            def compare(actual, wanted, path):
                if isinstance(wanted, dict):
                    if not isinstance(actual, dict) or set(actual) != set(wanted):
                        failures.append({"item": path, "reason": "keys"})
                    else:
                        for key in wanted:
                            compare(actual[key], wanted[key], f"{path}/{key}")
                    return
                key = path.rsplit("/", 1)[-1]
                if key == "joint_names":
                    if actual != wanted:
                        failures.append({"item": path, "reason": "canonical_joint_order"})
                    return
                if "/state/" in path or type(wanted) in (str, bool):
                    if type(actual) is not type(wanted) or actual != wanted:
                        failures.append({"item": path, "reason": "exact_state"})
                    return
                category = "environment_absolute"
                if "rotation_xyzw" in key:
                    error, category = angle(actual, wanted), "orientation_rad"
                elif key in ("position_m", "root_position_m"):
                    error, category = math.dist(actual, wanted), "position_m"
                else:
                    if "joint_positions" in key or key == "arrow_angle_rad":
                        category = "joint_rad"
                    elif "angular_velocity" in key or "joint_velocities" in key:
                        category = "angular_velocity_rad_s"
                    elif "linear_velocity" in key:
                        category = "linear_velocity_m_s"
                    if isinstance(wanted, list):
                        if not isinstance(actual, list) or len(actual) != len(wanted):
                            failures.append({"item": path, "reason": "shape"})
                            return
                        error = max(abs(a-b) for a, b in zip(actual, wanted))
                    else:
                        error = abs(actual-wanted)
                worst[category] = max(worst[category], error)
                if error > getattr(self.tolerances, category):
                    failures.append({"item": path, "reason": "tolerance", "deviation": error, "unit": category})
            compare(state, self._snapshot["state"], "state")
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            failures.append({"item": "state", "reason": "invalid_readback", "detail": str(exc)})
        result = {"reset_ok": not failures, "failures": failures, "worst_deviation": worst}
        if failures:
            self.exposure_ready = False
        return result

    def verify_current(self):
        try:
            return self.verify_state(self.adapter.read_state())
        except Exception as exc:
            self.exposure_ready = False
            return {"reset_ok": False, "failures": [{"item": "adapter", "reason": "read_failed", "detail": type(exc).__name__}], "worst_deviation": {}}

    def reset(self):
        self.exposure_ready = False
        started = time.monotonic_ns()
        try:
            if self.adapter.scene_sha256 != self._snapshot["scene_sha256"]:
                raise ValueError("scene fingerprint mismatch")
            self.adapter.write_state(self.neutral_state)
            self.adapter.step_fixed(self._snapshot["fixed_steps"])
            result = self.verify_current()
        except Exception as exc:
            result = {"reset_ok": False, "failures": [{"item": "adapter", "reason": "restore_failed", "detail": type(exc).__name__}], "worst_deviation": {}}
        result.update(reset_snapshot_sha256=self.reset_snapshot_sha256,
                      host_mono_ms=time.monotonic_ns()/1e6, sim_time=float(self.adapter.sim_time),
                      verification_elapsed_ms=(time.monotonic_ns()-started)/1e6)
        # Sink failure cannot produce successful return or open the exposure gate.
        self.event_sink(deepcopy(result))
        self.exposure_ready = result["reset_ok"]
        return result
