"""Rater panel protocol between the panel server (#21) and the rater stations (#21, #22).

Transport: one WebSocket per station at `WS_PATH`; JSON text frames, one message per
frame, each validated against `generation/schema/rater-message.schema.json` (a `oneOf`
over the message types below, selected by `type`). Audio assets are fetched with
`GET ASSET_PATH` and identified by `asset_id`, the lowercase hex SHA-256 of the canonical
WAV file, so the station checks the hash of the bytes it preloads against the ID.

Station -> server: `hello`, `sync_request`, `asset_ready`, `played`, `rating`, `withdraw`.
Server -> station: `welcome`, `sync_reply`, `preload`, `slot`, `rating_ack`, `pause`,
`resume`, `end`, `error`.

Masking: no message carries a method label, a book ID, a proposal-slot ID or a seed. A
slot is named by its rating-slot ID (`<batch>.<atom>.r<round>p<position>`). Ratings are
integers 1..7 and a binary comfort choice; the schema refuses any other field, so free
text cannot be submitted. Bot raters (#22) speak exactly this protocol; `hello` carries
the station's `kind` (`human` or `bot`), which must match its seat in the batch config
(`E_UNKNOWN_RATER` otherwise). The server side is `av_generation.panel` (#21) over the
session host contract `av_generation.panel_session` (#20).

Times: `*_server_ms` are server run-clock milliseconds (`av_generation.clock`); stations
estimate the offset with `sync_request`/`sync_reply` and schedule audio on it.
"""

from __future__ import annotations

from typing import Any, Final

from av_sound.recipe import StrictJsonError, strict_json_loads

from av_generation._schemas import schema_errors

PROTOCOL_VERSION: Final = 1
SCHEMA: Final = "rater-message.schema.json"

WS_PATH: Final = "/panel/ws"
ASSET_PATH: Final = "/panel/assets/{asset_id}.wav"
STATION_PAGE: Final = "/panel/station"

STATION_MESSAGES: Final[tuple[str, ...]] = (
    "hello",
    "sync_request",
    "asset_ready",
    "played",
    "rating",
    "withdraw",
)
SERVER_MESSAGES: Final[tuple[str, ...]] = (
    "welcome",
    "sync_reply",
    "preload",
    "slot",
    "rating_ack",
    "pause",
    "resume",
    "end",
    "error",
)
MESSAGE_TYPES: Final[tuple[str, ...]] = STATION_MESSAGES + SERVER_MESSAGES

RATING_ERROR_CODES: Final[tuple[str, ...]] = (
    "E_PROTOCOL",
    "E_UNKNOWN_RATER",
    "E_UNKNOWN_SLOT",
    "E_SLOT_CLOSED",
    "E_LOCKED",
    "E_DUPLICATE_RATING",
    "E_PLACEHOLDER",
    "E_FIRST_ATOM",
)
"""Codes in `rating_ack.code` / `error.code`."""


class RaterProtocolError(ValueError):
    """A message is not valid JSON or does not match the protocol schema."""

    def __init__(self, message: str, errors: tuple[str, ...] = ()) -> None:
        super().__init__(message if not errors else f"{message}: {'; '.join(errors[:5])}")
        self.errors = errors


def message_errors(message: object) -> tuple[str, ...]:
    """Schema errors of a decoded message (empty when valid)."""
    return schema_errors(SCHEMA, message)


def parse_message(text: str | bytes, *, sender: str | None = None) -> dict[str, Any]:
    """Decode and validate one frame. `sender` (`station`/`server`) restricts the types."""
    try:
        data = strict_json_loads(text)
    except StrictJsonError as err:
        raise RaterProtocolError(f"not strict JSON: {err}") from err
    errors = message_errors(data)
    if errors:
        raise RaterProtocolError("message does not match the rater protocol", errors)
    assert isinstance(data, dict)
    allowed = {"station": STATION_MESSAGES, "server": SERVER_MESSAGES}.get(sender or "")
    if allowed is not None and data["type"] not in allowed:
        raise RaterProtocolError(f"a {sender} may not send {data['type']!r}")
    return data
