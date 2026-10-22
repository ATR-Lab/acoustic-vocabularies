"""Version 2 deliberately separates public render state from private commands.

No generic dict serialization of simulator state is permitted. The immutable
registry is built from the verified layout/snapshot, never the next trial.
"""
from __future__ import annotations

import json
import math
import re
import time
import uuid
from dataclasses import dataclass

HEX = re.compile(r"[0-9a-f]{64}\Z")
SESSION = re.compile(r"[0-9a-f]{32}\Z")
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_/.-]{0,95}\Z")
STATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
FIELDS = {"version", "kind", "source_kind", "station_id", "scene_sha256",
          "reset_snapshot_sha256", "session_id", "seq", "host_monotonic_ns",
          "sim_time", "sim_step", "joint_names", "joint_positions", "objects"}
OBJECT_FIELDS = {"id", "position_m", "rotation_xyzw", "visible", "enabled", "state"}
VISUAL_FIELDS = {"card_face", "arrow_angle_rad", "lid_open_fraction", "tag_attached", "location"}


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def vector(value, length):
    return isinstance(value, list) and len(value) == length and all(number(x) for x in value)


def visual_state(state, anchors):
    if not isinstance(state, dict) or set(state) - VISUAL_FIELDS:
        raise ValueError("Unexpected visual-state field")
    for key, value in state.items():
        valid = False
        if key == "card_face":
            valid = type(value) is int and value in (0, 1)
        elif key == "arrow_angle_rad":
            valid = number(value) and -math.tau <= value <= math.tau
        elif key == "lid_open_fraction":
            valid = number(value) and 0 <= value <= 1
        elif key == "tag_attached":
            valid = type(value) is bool
        elif key == "location":
            valid = isinstance(value, str) and value in anchors
        if not valid:
            raise ValueError("Invalid visual-state value")


@dataclass(frozen=True)
class PublicRegistry:
    station_id: str
    scene_sha256: str
    reset_snapshot_sha256: str
    joint_names: tuple[str, ...]
    object_states: tuple[tuple[str, tuple[str, ...]], ...]
    anchor_ids: tuple[str, ...] = ()

    def __post_init__(self):
        if not STATION.fullmatch(self.station_id):
            raise ValueError("Use a logical station identifier (1..80 safe characters)")
        if not HEX.fullmatch(self.scene_sha256) or not HEX.fullmatch(self.reset_snapshot_sha256):
            raise ValueError("Verified scene and neutral snapshot hashes required")
        if len(self.joint_names) != 43 or len(set(self.joint_names)) != 43:
            raise ValueError("Exactly the 43 measured canonical joints are required")
        if any(not isinstance(n, str) or not IDENTIFIER.fullmatch(n) for n in self.joint_names):
            raise ValueError("Invalid canonical joint identifier")
        ids = [obj_id for obj_id, _ in self.object_states]
        if not ids or ids != sorted(set(ids)) or any(not IDENTIFIER.fullmatch(x) for x in ids):
            raise ValueError("Object registry must be sorted, unique and nonempty")
        for _, keys in self.object_states:
            if tuple(sorted(set(keys))) != keys or set(keys) - VISUAL_FIELDS:
                raise ValueError("Unexpected object state contract")
        if len(set(self.anchor_ids)) != len(self.anchor_ids) or any(not IDENTIFIER.fullmatch(x) for x in self.anchor_ids):
            raise ValueError("Invalid physical anchor registry")

    @property
    def anchors(self):
        return set(self.anchor_ids) | {name for name, _ in self.object_states}


def validate_frame(frame, registry: PublicRegistry):
    if not isinstance(frame, dict) or set(frame) != FIELDS:
        raise ValueError("Unexpected public envelope fields")
    if type(frame["version"]) is not int or frame["version"] != 2 or frame["kind"] != "state":
        raise ValueError("Unsupported public state version")
    if frame["source_kind"] not in ("live", "synthetic"):
        raise ValueError("Explicit source provenance required")
    for key in ("station_id", "scene_sha256", "reset_snapshot_sha256"):
        if frame[key] != getattr(registry, key):
            raise ValueError("Wrong station, scene or neutral snapshot")
    if not isinstance(frame["session_id"], str) or not SESSION.fullmatch(frame["session_id"]):
        raise ValueError("Invalid publisher epoch")
    for key in ("seq", "sim_step"):
        if type(frame[key]) is not int or frame[key] < 0:
            raise ValueError("Invalid counter")
    stamp = frame["host_monotonic_ns"]
    if not isinstance(stamp, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,19})", stamp):
        raise ValueError("Monotonic nanoseconds must be a decimal string")
    if not number(frame["sim_time"]) or frame["sim_time"] < 0:
        raise ValueError("Invalid simulation time")
    if frame["joint_names"] != list(registry.joint_names) or not vector(frame["joint_positions"], 43):
        raise ValueError("Canonical joint order/count/finite check failed")
    objects = frame["objects"]
    if not isinstance(objects, list) or len(objects) != len(registry.object_states):
        raise ValueError("Incomplete scene state")
    # Registry authority is immutable for this frame. Rebuilding the same set
    # once per object adds work without performing an additional value check.
    anchors = registry.anchors
    for obj, (obj_id, keys) in zip(objects, registry.object_states):
        if not isinstance(obj, dict) or set(obj) != OBJECT_FIELDS or obj["id"] != obj_id:
            raise ValueError("Unregistered, missing or unordered object")
        if not vector(obj["position_m"], 3) or not vector(obj["rotation_xyzw"], 4):
            raise ValueError("Invalid object pose")
        if abs(sum(v*v for v in obj["rotation_xyzw"]) - 1) > 1e-5:
            raise ValueError("Object quaternion is not normalized")
        if type(obj["visible"]) is not bool or type(obj["enabled"]) is not bool:
            raise ValueError("Visibility/enabled must be boolean")
        if not isinstance(obj["state"], dict) or tuple(sorted(obj["state"])) != keys:
            raise ValueError("Object visual-state contract mismatch")
        visual_state(obj["state"], anchors)
    return frame


def encode(frame):
    return json.dumps(frame, separators=(",", ":"), allow_nan=False, ensure_ascii=True)


def strict_loads(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = value
        return result
    def reject_constant(_):
        raise ValueError("Nonfinite JSON number")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=reject_constant)


STAMP_TEXT = re.compile(r"(?:0|[1-9][0-9]{0,19})\Z")
# The envelope is the only wire object with this key (validate_frame enforces
# closed key sets), and a JSON key is the only string followed by ':'.
_PROVISIONAL_STAMP = '"host_monotonic_ns":"0"'


class PreparedFrame:
    """A validated frame whose host stamp is applied at publication time.

    The payload is held as the exact bytes before and after the stamp value, so
    committing costs a string join rather than a second projection/encode.
    """
    __slots__ = ("frame", "head", "tail", "seq", "session_id", "committed")

    def __init__(self, frame, head, tail, seq, session_id):
        self.frame, self.head, self.tail = frame, head, tail
        self.seq, self.session_id, self.committed = seq, session_id, False


class StateEncoder:
    def __init__(self, registry, source_kind="live", clock_ns=time.monotonic_ns):
        if source_kind not in ("live", "synthetic"):
            raise ValueError("Invalid source provenance")
        self.registry, self.source_kind, self.clock_ns = registry, source_kind, clock_ns
        self.session_id, self.sequence = uuid.uuid4().hex, 0

    def prepare(self, positions, object_state, sim_time, sim_step):
        """Project, validate and encode now; the stamp is supplied by commit().

        The returned frame is fully validated with a provisional stamp. It does
        not consume a sequence number until it is committed.
        """
        frame = self._frame(positions, object_state, sim_time, sim_step, lambda: "0")
        validate_frame(frame, self.registry)
        payload = encode(frame)
        if payload.count(_PROVISIONAL_STAMP) != 1:
            raise ValueError("Envelope stamp position is not unique")
        head, tail = payload.split(_PROVISIONAL_STAMP)
        return PreparedFrame(frame, head, tail, self.sequence, self.session_id)

    def commit(self, prepared, stamp_ns):
        """Apply the publication stamp; output equals encode(frame) byte for byte."""
        if (not isinstance(prepared, PreparedFrame) or prepared.committed
                or prepared.seq != self.sequence or prepared.session_id != self.session_id):
            raise ValueError("Stale or already committed prepared frame")
        if type(stamp_ns) is not int or stamp_ns < 0:
            raise ValueError("Monotonic nanoseconds must be a non-negative integer")
        text = str(stamp_ns)
        if not STAMP_TEXT.fullmatch(text):
            raise ValueError("Monotonic nanoseconds must be a decimal string")
        prepared.frame["host_monotonic_ns"] = text
        prepared.committed = True
        self.sequence += 1
        return prepared.frame, prepared.head + '"host_monotonic_ns":"' + text + '"' + prepared.tail

    def build(self, positions, object_state, sim_time, sim_step):
        frame = self._frame(positions, object_state, sim_time, sim_step, lambda: str(self.clock_ns()))
        validate_frame(frame, self.registry)
        self.sequence += 1
        return frame

    def _frame(self, positions, object_state, sim_time, sim_step, stamp):
        # Explicit projection: never serialize the complete simulation/command object.
        if set(object_state) != {obj_id for obj_id, _ in self.registry.object_states}:
            raise ValueError("Scene inventory changed")
        objects = []
        for obj_id, _ in self.registry.object_states:
            source = object_state[obj_id]
            # Accepted public leaves are scalar schema primitives. Detach the
            # three containers explicitly; strict validation below still checks
            # every value and rejects nested or unsupported visual data. Keep
            # the original list/dict input contract (do not coerce tuples).
            if (not isinstance(source["position_m"], list) or
                    not isinstance(source["rotation_xyzw"], list) or
                    not isinstance(source["state"], dict)):
                raise ValueError("Invalid public object containers")
            objects.append({"id": obj_id, "position_m": list(source["position_m"]),
                            "rotation_xyzw": list(source["rotation_xyzw"]),
                            "visible": source["visible"], "enabled": source["enabled"],
                            "state": dict(source["state"])})
        frame = dict(version=2, kind="state", source_kind=self.source_kind,
                     station_id=self.registry.station_id, scene_sha256=self.registry.scene_sha256,
                     reset_snapshot_sha256=self.registry.reset_snapshot_sha256,
                     session_id=self.session_id, seq=self.sequence,
                     host_monotonic_ns=stamp(), sim_time=sim_time, sim_step=sim_step,
                     joint_names=list(self.registry.joint_names), joint_positions=list(positions), objects=objects)
        return frame
