"""Public state-only envelope shared by the two Phase 1 transport candidates."""
import json
import math
import time
import uuid

FIELDS = {"version", "kind", "source_kind", "session_id", "seq", "host_monotonic_ns",
          "sim_time", "sim_step", "joint_names", "joint_positions", "objects"}


def finite_numbers(values):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)


def validate_frame(frame, expected_joints=None):
    if set(frame) != FIELDS:
        raise ValueError("Unknown/missing fields; only public scene state may cross this bridge")
    if frame["version"] != 1 or frame["kind"] != "state" or frame["source_kind"] not in ("live", "synthetic"):
        raise ValueError("Unsupported state envelope")
    if not isinstance(frame["session_id"], str) or len(frame["session_id"]) != 32:
        raise ValueError("session_id must be a random non-identifying 32-character token")
    for key in ("seq", "sim_step"):
        if type(frame[key]) is not int or frame[key] < 0:
            raise ValueError("Invalid integer counter")
    if not isinstance(frame["host_monotonic_ns"], str) or not frame["host_monotonic_ns"].isdigit():
        raise ValueError("Host nanoseconds must be an unsigned decimal string")
    names, positions = frame["joint_names"], frame["joint_positions"]
    if not isinstance(names, list) or not names or len(names) != len(set(names)) or not all(isinstance(n, str) and n for n in names):
        raise ValueError("Joint names must be unique and nonempty")
    if len(names) != len(positions) or not finite_numbers(positions + [frame["sim_time"]]):
        raise ValueError("Joint count or finite-value failure")
    if expected_joints is not None and names != expected_joints:
        raise ValueError("Canonical joint order mismatch")
    if not isinstance(frame["objects"], list):
        raise ValueError("objects must be a list")
    seen = set()
    for obj in frame["objects"]:
        if set(obj) != {"id", "position_m", "rotation_xyzw"} or not obj["id"].startswith("placeholder_") or obj["id"] in seen:
            raise ValueError("Only uniquely named public placeholder poses are allowed")
        seen.add(obj["id"])
        if len(obj["position_m"]) != 3 or len(obj["rotation_xyzw"]) != 4 or not finite_numbers(obj["position_m"] + obj["rotation_xyzw"]):
            raise ValueError("Invalid object pose")
        if abs(sum(v * v for v in obj["rotation_xyzw"]) - 1) > .01:
            raise ValueError("Quaternion must be normalized")
    return frame


class FrameBuilder:
    def __init__(self, joint_names, source_kind="live"):
        self.joint_names = list(joint_names)
        self.source_kind = source_kind
        self.session_id = uuid.uuid4().hex
        self.seq = 0

    def build(self, sim_time, sim_step, positions, objects=None):
        frame = dict(version=1, kind="state", source_kind=self.source_kind, session_id=self.session_id,
                     seq=self.seq, host_monotonic_ns=str(time.monotonic_ns()), sim_time=sim_time,
                     sim_step=sim_step, joint_names=self.joint_names, joint_positions=list(positions), objects=objects or [])
        validate_frame(frame, self.joint_names)
        self.seq += 1
        return frame


def encode(frame):
    return json.dumps(frame, separators=(",", ":"), allow_nan=False)


def echo_reply(request, received_ns=None):
    if set(request) != {"kind", "c0_s"} or request["kind"] != "echo" or not math.isfinite(float(request["c0_s"])):
        raise ValueError("Echo carries only a client monotonic timestamp")
    return {"kind": "echo", "c0_s": request["c0_s"],
            "s1_ns": str(received_ns if received_ns is not None else time.monotonic_ns()),
            "s2_ns": str(time.monotonic_ns())}
