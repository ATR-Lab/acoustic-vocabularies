"""Validate a PRIVATE foundation station configuration. Never print its contents."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON property")
        result[key] = value
    return result


def validate(path: Path, protocol: str, topology: str) -> dict:
    def invalid_constant(_):
        raise ValueError("non-finite JSON number")
    text = path.read_text(encoding="utf-8")
    if len(text) > 65536:
        raise ValueError("configuration too large")
    value = json.loads(text, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    schema = json.loads((Path(__file__).resolve().parents[1] / "apparatus/schemas/station.schema.json").read_text())
    if list(Draft202012Validator(schema).iter_errors(value)):
        raise ValueError("station schema validation failed")
    if value["provisioning_status"] != "provisioned":
        raise ValueError("example is not provisioned")
    if value["protocol_version"] != protocol or value["topology"] != topology:
        raise ValueError("protocol or topology mismatch")
    endpoint = urlsplit(value["isaac_endpoint"])
    if endpoint.scheme not in ("ws", "wss") or not endpoint.hostname or endpoint.username is not None or endpoint.password is not None or endpoint.fragment:
        raise ValueError("endpoint invalid")
    quaternion = value["observer_reference"]["rotation_xyzw"]
    if abs(sum(x*x for x in quaternion) - 1) > .0001 or any(not math.isfinite(x) for x in quaternion):
        raise ValueError("reference quaternion invalid")
    if abs(quaternion[0]) > .0001 or abs(quaternion[2]) > .0001:
        raise ValueError("reference must preserve world up")
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--topology", required=True, choices=("standalone_quest", "pcvr_link"))
    args = parser.parse_args()
    try:
        validate(args.path, args.protocol, args.topology)
    except (ValueError, OSError, KeyError):
        raise SystemExit("Station configuration rejected; no private values emitted.")
    print("Station configuration valid for specified protocol and topology.")
