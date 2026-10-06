"""Read-only health messages on the already authenticated private WebSocket.

These are not commands: they never enter the simulation queue, idempotency cache
or durable command log. The provider must be CommandQueue.health, which returns
a detached cached status and advances its existing source ages without USD reads.
"""
from copy import deepcopy
import re


REQUEST_KIND = "private_health_probe"
REPLY_KIND = "private_health_reply"
REQUEST_KEYS = frozenset(("version", "kind", "control_session_id", "request_id"))


def valid_id(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def reply(value, expected_session_id, health_provider):
    """Return one correlated response; never manufacture freshness or mutate state.

    The transport performs strict JSON decoding and authenticated peer admission
    before routing a message here. Provider failure propagates to close the
    connection; there is no optimistic health fallback.
    """
    if not valid_id(expected_session_id):
        raise ValueError("Pinned private control session required")
    identifier = value.get("request_id") if isinstance(value, dict) else None
    response = dict(version=1, kind=REPLY_KIND, control_session_id=expected_session_id,
                    request_id=identifier if valid_id(identifier) else None,
                    accepted=False, reason="MALFORMED_PROBE", health=None)
    if (not isinstance(value, dict) or set(value) != REQUEST_KEYS
            or type(value["version"]) is not int or value["version"] != 1
            or value["kind"] != REQUEST_KIND or not valid_id(identifier)
            or not valid_id(value["control_session_id"])):
        return response
    if value["control_session_id"] != expected_session_id:
        response["reason"] = "CONTROL_SESSION_MISMATCH"
        return response
    health = health_provider()
    if not isinstance(health, dict) or health.get("control_session_id") != expected_session_id:
        response["reason"] = "HEALTH_SESSION_CHANGED"
        return response
    # Detached return preserves the provider's exact timestamp, ages and flags.
    response.update(accepted=True, reason="HEALTH", health=deepcopy(health))
    return response
