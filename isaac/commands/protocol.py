"""Strict private engineering command envelope, independent of public wire v2."""
import hashlib
import json
import re
import math

MODES = ("teaching", "test", "post_endpoint")
COMMANDS = ("hold_neutral", "demo", "reset", "set_mode", "pause", "resume", "stop", "health")
LEGAL_PAIRS = frozenset([(action, "tray_"+target) for action in
    ("ADD_ONE", "REMOVE_ONE", "FLIP_CARD", "ALIGN_ARROW") for target in "ABCD"] +
    [(action, "container_"+target) for action in ("SCAN", "TAG", "CLOSE", "QUARANTINE") for target in "EFGH"])


def decode(raw):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 16384:
        raise ValueError("text command maximum is 16384 bytes")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def reject_constant(_):
        raise ValueError("nonfinite JSON constant")
    def finite_float(text):
        value = float(text)
        if not math.isfinite(value):
            raise ValueError("nonfinite JSON number")
        return value
    return json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant, parse_float=finite_float)


def target_bearing(value):
    if isinstance(value, dict):
        return value.get("command") == "demo" or any(k in ("action", "target") or target_bearing(v) for k, v in value.items())
    if isinstance(value, list):
        return any(target_bearing(v) for v in value)
    return False


def validate(value):
    if not isinstance(value, dict) or set(value) != {"version", "kind", "control_session_id", "request_id", "command", "args"}:
        raise ValueError("exact command envelope required")
    if type(value["version"]) is not int or value["version"] != 1 or value["kind"] != "private_command":
        raise ValueError("unsupported private command protocol")
    if not isinstance(value["request_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", value["request_id"]):
        raise ValueError("opaque request_id required")
    if not isinstance(value["control_session_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", value["control_session_id"]):
        raise ValueError("private control session identity required")
    command, args = value["command"], value["args"]
    if not isinstance(command, str) or command not in COMMANDS or not isinstance(args, dict):
        raise ValueError("unknown command or arguments")
    expected = {"action", "target"} if command == "demo" else {"mode"} if command == "set_mode" else set()
    if set(args) != expected:
        raise ValueError("exact command arguments required")
    if command == "demo" and (type(args["action"]) is not str or type(args["target"]) is not str or (args["action"], args["target"]) not in LEGAL_PAIRS):
        raise ValueError("illegal action/target pair")
    if command == "set_mode" and args["mode"] not in MODES:
        raise ValueError("unknown mode")
    return value


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
