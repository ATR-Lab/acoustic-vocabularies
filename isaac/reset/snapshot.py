"""Versioned neutral data. No simulator import is needed for validation."""
from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from pathlib import Path


def canonical_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def sha256(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def keys(value, expected, path):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f"{path}: exact keys required")


def number(value, path):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{path}: finite number required")


def vector(value, length, path):
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f"{path}: length {length} required")
    for item in value:
        number(item, path)


def quaternion(value, path):
    vector(value, 4, path)
    if abs(sum(x*x for x in value) - 1) > 1e-5:
        raise ValueError(f"{path}: unit quaternion required")


def primitive_tree(value, path):
    """Environment authoring values are finite scalars/vectors, never opaque data."""
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError(f"{path}: nonempty property name required")
            primitive_tree(item, f"{path}/{key}")
    elif isinstance(value, list):
        if not 1 <= len(value) <= 4:
            raise ValueError(f"{path}: environment vector length must be 1..4")
        for item in value:
            number(item, path)
    elif type(value) is bool:
        pass
    else:
        number(value, path)


OBJECT_KEYS = {"position_m", "rotation_xyzw", "visible", "enabled", "collision_enabled",
               "linear_velocity_m_s", "angular_velocity_rad_s", "state"}
STATE_KEYS = {"card_face", "arrow_angle_rad", "lid_open_fraction", "tag_attached", "location"}


def validate_state(state, neutral=False):
    keys(state, {"robot", "objects", "environment", "frames"}, "state")
    robot = state["robot"]
    keys(robot, {"joint_names", "joint_positions_rad", "joint_velocities_rad_s", "root_position_m",
                 "root_rotation_xyzw", "root_linear_velocity_m_s", "root_angular_velocity_rad_s"}, "robot")
    names = robot["joint_names"]
    if not isinstance(names, list) or len(names) != 43 or len(set(names)) != 43 or any(not isinstance(n, str) or not n for n in names):
        raise ValueError("robot: exactly 43 unique canonical joint names required")
    for key in ("joint_positions_rad", "joint_velocities_rad_s"):
        vector(robot[key], 43, key)
    for key in ("root_position_m", "root_linear_velocity_m_s", "root_angular_velocity_rad_s"):
        vector(robot[key], 3, key)
    quaternion(robot["root_rotation_xyzw"], "root_rotation_xyzw")
    if not isinstance(state["objects"], dict) or not state["objects"]:
        raise ValueError("nonempty objects mapping required")
    for name, obj in state["objects"].items():
        if not isinstance(name, str) or not name:
            raise ValueError("object id required")
        keys(obj, OBJECT_KEYS, name)
        for key in ("position_m", "linear_velocity_m_s", "angular_velocity_rad_s"):
            vector(obj[key], 3, f"{name}/{key}")
        quaternion(obj["rotation_xyzw"], name)
        for key in ("visible", "enabled", "collision_enabled"):
            if type(obj[key]) is not bool:
                raise ValueError(f"{name}/{key}: boolean required")
        discrete = obj["state"]
        if not isinstance(discrete, dict) or set(discrete) - STATE_KEYS:
            raise ValueError(f"{name}/state: unknown state property")
        for key, value in discrete.items():
            if key == "card_face" and (type(value) is not int or value not in (0, 1)):
                raise ValueError("card_face must be 0 or 1")
            if key == "tag_attached" and type(value) is not bool:
                raise ValueError("tag_attached must be boolean")
            if key == "location" and (not isinstance(value, str) or not value):
                raise ValueError("location must name a physical anchor")
            if key in ("arrow_angle_rad", "lid_open_fraction"):
                number(value, key)
            if key == "lid_open_fraction" and not 0 <= value <= 1:
                raise ValueError("lid_open_fraction outside 0..1")
    keys(state["environment"], {"materials", "lights"}, "environment")
    for category in state["environment"].values():
        if not isinstance(category, dict):
            raise ValueError("environment category must be mapping")
        primitive_tree(category, "environment")
    if not isinstance(state["frames"], dict) or not state["frames"]:
        raise ValueError("head/hand observer frames required")
    for name, frame in state["frames"].items():
        keys(frame, {"position_m", "rotation_xyzw"}, f"frame/{name}")
        vector(frame["position_m"], 3, name)
        quaternion(frame["rotation_xyzw"], name)
    if neutral:
        velocities = [robot["joint_velocities_rad_s"], robot["root_linear_velocity_m_s"], robot["root_angular_velocity_rad_s"]]
        velocities += [obj[key] for obj in state["objects"].values() for key in ("linear_velocity_m_s", "angular_velocity_rad_s")]
        if any(value != 0 for values in velocities for value in values):
            raise ValueError("neutral snapshot requires exactly zero commanded velocities")


def validate_snapshot(value):
    keys(value, {"schema_version", "scene_sha256", "state", "fixed_steps", "coordinate_frame"}, "snapshot")
    if value["schema_version"] != "1.0.0" or value["coordinate_frame"] != "usd_world_rh_z_up_xyzw":
        raise ValueError("unsupported snapshot version/frame")
    if not isinstance(value["scene_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["scene_sha256"]):
        raise ValueError("scene SHA-256 required")
    if type(value["fixed_steps"]) is not int or not 1 <= value["fixed_steps"] <= 10:
        raise ValueError("fixed_steps must be 1..10")
    validate_state(value["state"], neutral=True)


def capture(adapter, fixed_steps=1):
    value = {"schema_version": "1.0.0", "coordinate_frame": "usd_world_rh_z_up_xyzw",
             "scene_sha256": adapter.scene_sha256, "fixed_steps": fixed_steps,
             "state": deepcopy(adapter.read_state())}
    # Capture requires an already established neutral; do not silently erase
    # measured nonzero velocity or overwrite a prior snapshot.
    validate_snapshot(value)
    return value


def snapshot_bytes(value):
    validate_snapshot(value)
    return canonical_bytes(value)


def load_snapshot(path, expected_sha256):
    raw = Path(path).read_bytes()
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("configured snapshot SHA-256 required")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("snapshot SHA-256 mismatch")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique)
    validate_snapshot(value)
    return value
