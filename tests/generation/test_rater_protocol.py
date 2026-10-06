"""Rater panel protocol messages (#20 <-> #21): exact JSON Schema, no free text, masking."""

import json

import pytest

from av_generation._schemas import load_schema
from av_generation.masking import masking_findings
from av_generation.rater_protocol import (
    MESSAGE_TYPES,
    SERVER_MESSAGES,
    STATION_MESSAGES,
    RaterProtocolError,
    message_errors,
    parse_message,
)

H = "b" * 64
RSID = "DEMO-A-P01.K-a1.r2p5"

EXAMPLES = {
    "hello": {
        "type": "hello",
        "protocol_version": 1,
        "station": "S1",
        "rater_id": "R1",
        "client_ms": 12.5,
        "resume_rating_slot_id": None,
    },
    "sync_request": {"type": "sync_request", "seq": 3, "client_ms": 1000.25},
    "asset_ready": {"type": "asset_ready", "asset_id": H, "ok": True},
    "played": {
        "type": "played",
        "rating_slot_id": RSID,
        "role": "reference",
        "asset_id": H,
        "scheduled_server_ms": 12_000,
        "onset_server_ms": 12_004,
    },
    "rating": {
        "type": "rating",
        "rating_slot_id": RSID,
        "association": 7,
        "distinguishability": 1,
        "comfort": "unacceptable",
        "rt_ms": 3000,
    },
    "withdraw": {"type": "withdraw", "reason": "discomfort"},
    "welcome": {
        "type": "welcome",
        "protocol_version": 1,
        "session_id": "DEMO-session-1",
        "station": "S1",
        "server_ms": 99,
        "state": "waiting",
    },
    "sync_reply": {"type": "sync_reply", "seq": 3, "client_ms": 1000.25, "server_ms": 5000},
    "preload": {
        "type": "preload",
        "assets": [{"asset_id": H, "url": f"/panel/assets/{H}.wav", "n_bytes": 57_644}],
    },
    "slot": {
        "type": "slot",
        "rating_slot_id": RSID,
        "position": 5,
        "start_server_ms": 10_000,
        "duration_ms": 20_000,
        "placeholder": False,
        "meaning": "ALIGN_ARROW",
        "candidate": {"asset_id": H, "offset_ms": 0},
        "reference": {"asset_id": "c" * 64, "offset_ms": 2000, "meaning": "FLIP_CARD"},
        "ask_distinguishability": True,
        "unlock_offset_ms": 2900,
        "lock_offset_ms": 20_000,
        "rejoin": False,
    },
    "rating_ack": {
        "type": "rating_ack",
        "rating_slot_id": RSID,
        "accepted": False,
        "code": "E_LOCKED",
    },
    "pause": {"type": "pause", "reason": "between_atoms"},
    "resume": {"type": "resume"},
    "end": {"type": "end", "reason": "appointment_complete"},
    "error": {"type": "error", "code": "E_PROTOCOL", "message": "bad frame"},
}


def test_every_type_has_a_valid_example():
    assert set(EXAMPLES) == set(MESSAGE_TYPES)
    assert set(load_schema("rater-message.schema.json")["$defs"]) == set(MESSAGE_TYPES)
    for name, message in EXAMPLES.items():
        assert message_errors(message) == (), name
        sender = "station" if name in STATION_MESSAGES else "server"
        assert parse_message(json.dumps(message), sender=sender) == message


@pytest.mark.parametrize(
    "change",
    [
        {"association": 0},
        {"association": 8},
        {"association": 4.5},
        {"distinguishability": 9},
        {"comfort": "ok"},
        {"comment": "sounds harsh"},
        {"association": None},
        {"rt_ms": -1},
    ],
)
def test_ratings_outside_the_scale_or_with_free_text_are_rejected(change):
    message = dict(EXAMPLES["rating"], **change)
    with pytest.raises(RaterProtocolError):
        parse_message(json.dumps(message), sender="station")


def test_direction_and_json_strictness():
    with pytest.raises(RaterProtocolError):
        parse_message(json.dumps(EXAMPLES["slot"]), sender="station")
    with pytest.raises(RaterProtocolError):
        parse_message(json.dumps(EXAMPLES["rating"]), sender="server")
    with pytest.raises(RaterProtocolError):
        parse_message('{"type": "resume", "type": "resume"}')
    with pytest.raises(RaterProtocolError):
        parse_message(json.dumps(dict(EXAMPLES["slot"], book_id="BK-C-7QX4MN")))
    assert SERVER_MESSAGES and STATION_MESSAGES


def test_messages_carry_no_method_labels():
    for message in EXAMPLES.values():
        assert masking_findings(json.dumps(message)) == ()
