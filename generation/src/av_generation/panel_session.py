"""Panel session contract between the orchestrator (#20, host) and the panel server (#21).

The orchestrator implements `PanelSessionHost`; the panel server (`av_generation.panel`,
#21) serves the rater stations over `rater_protocol` and talks to the session only
through this protocol. The types carry no book ID, proposal-slot ID, method or seed, so
nothing the panel server holds can leak them to a station (masking by construction).

Division of work:

- **Host (#20)** schedules everything on the run clock and publishes `PanelEvent`s
  (`preload`, `slot`, `pause`, `resume`, `end`) with increasing `seq`. It is the only
  writer of the panel logs: `rating` records, `play` records (`rating_candidate`,
  `rating_reference`) and the panel `timing` events (`station_connect`,
  `station_disconnect`, `station_reconnect`, `clock_sync`, `asset_ready`,
  `rater_withdrawal`, `rating_window_*`). At each slot's lock time (start + 20 s) it
  writes exactly one `RatingRecord` per seat (`BatchConfig.panel.raters`): the accepted
  submission, a `placeholder` record (invalid candidate) or a `missing` record (no
  rating; `reconnected=true` if the station rejoined during the slot). So every seat
  gets 9 rating records per round, and `rater_kind` comes from the seat.
- **Panel server (#21)** accepts WebSockets at `rater_protocol.WS_PATH`, validates every
  frame (`parse_message`), maps a `hello` to `station_joined`, forwards `asset_ready`,
  `sync_request` results, `played`, `rating` and `withdraw` to the host, and broadcasts
  the host's events as server messages. One task waits on `wait_events` in a worker
  thread (`asyncio.to_thread`) and fans out. A station that (re)joins gets `welcome`
  from `snapshot()`, the pending `preload`, and the current `slot` with `rejoin=true`;
  it plays no asset whose scheduled onset has passed (no replay).

Every host method is thread-safe and returns quickly, except `wait_events`, which blocks
up to `timeout_s`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal, Protocol, runtime_checkable

from av_generation.config import RaterSeat
from av_generation.constants import CANDIDATE_ONSET_MS, REFERENCE_ONSET_MS

PANEL_EVENT_KINDS: Final[tuple[str, ...]] = ("preload", "slot", "pause", "resume", "end")
PAUSE_REASONS: Final[tuple[str, ...]] = ("operator", "between_atoms", "break")
END_REASONS: Final[tuple[str, ...]] = (
    "batch_complete",
    "appointment_complete",
    "withdrawn",
    "aborted",
)
"""`pause.reason` and `end.reason` values of `rater-message.schema.json`."""


class PanelRefused(RuntimeError):
    """The host refuses a station request; `.code` is a `rater_protocol` error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True, slots=True)
class AssetRef:
    """A WAV the panel serves: `asset_id` is the file SHA-256 (`rater_protocol`)."""

    asset_id: str
    pcm_sha256: str
    n_samples: int
    n_bytes: int


@dataclass(frozen=True, slots=True)
class PanelSlot:
    """One scheduled rating slot as the panel server sees it (the `slot` message source)."""

    rating_slot_id: str
    position: int
    """1..9 in the round's play order."""
    start_ms: int
    """Run-clock start of the slot (`slot.start_server_ms`)."""
    placeholder: bool
    """Invalid candidate: neutral placeholder for 20 s, no audio, no rating controls."""
    first_atom: bool
    """First atom of the book: no reference, distinguishability not asked (stored as 4)."""
    meaning: str | None
    """The candidate's intended meaning (`meanings`); `None` only for placeholders."""
    candidate: AssetRef | None
    reference: AssetRef | None
    """Nearest committed atom of the same book (silence: `None`)."""
    reference_meaning: str | None
    unlock_offset_ms: int
    """Controls unlock after the reference ends, or at 2 s without a reference."""

    @property
    def ask_distinguishability(self) -> bool:
        return not self.first_atom and not self.placeholder

    @property
    def candidate_offset_ms(self) -> int:
        return CANDIDATE_ONSET_MS

    @property
    def reference_offset_ms(self) -> int:
        return REFERENCE_ONSET_MS


@dataclass(frozen=True, slots=True)
class PanelEvent:
    """One event of the session schedule, broadcast to every station."""

    seq: int
    """1, 2, 3, ... in issue order."""
    kind: Literal["preload", "slot", "pause", "resume", "end"]
    t_ms: int
    slot: PanelSlot | None = None
    """`kind == "slot"` only."""
    assets: tuple[AssetRef, ...] = ()
    """`kind == "preload"`: the assets of the next round (candidates and references)."""
    reason: str | None = None
    """`pause` (`PAUSE_REASONS`) and `end` (`END_REASONS`) only."""


@dataclass(frozen=True, slots=True)
class PanelSnapshot:
    """What a (re)joining station needs: the session state now."""

    seq: int
    """`seq` of the last event issued (0 before the first)."""
    state: Literal["waiting", "slot", "paused", "between_atoms", "ended"]
    slot: PanelSlot | None
    """The slot in progress, if any."""
    preload: tuple[AssetRef, ...]
    """Assets of the current or next round."""


@dataclass(frozen=True, slots=True)
class PlayReport:
    """A station's `played` message (onset measured on the station, in server time)."""

    rater_id: str
    station: str
    rating_slot_id: str
    role: Literal["candidate", "reference"]
    asset_id: str
    scheduled_ms: int
    onset_ms: int
    received_ms: int


@dataclass(frozen=True, slots=True)
class RatingSubmission:
    """A station's `rating` message, schema-checked by the panel server."""

    rater_id: str
    station: str
    rating_slot_id: str
    association: int
    distinguishability: int | None
    comfort: Literal["acceptable", "unacceptable"]
    rt_ms: int
    received_ms: int
    """Run-clock time the server received it (lock checks use this)."""


@dataclass(frozen=True, slots=True)
class RatingAck:
    """The host's answer (`rating_ack`): `code` is `None` when accepted, else one of
    `rater_protocol.RATING_ERROR_CODES` (`E_UNKNOWN_SLOT`, `E_SLOT_CLOSED`, `E_LOCKED`,
    `E_DUPLICATE_RATING`, `E_PLACEHOLDER`, `E_FIRST_ATOM`)."""

    accepted: bool
    code: str | None = None


@runtime_checkable
class PanelSessionHost(Protocol):
    """What the panel server (#21) needs from the orchestrator (#20)."""

    run_id: str

    def seats(self) -> Sequence[RaterSeat]:
        """The panel's raters (`BatchConfig.panel.raters`)."""
        ...

    def wait_events(self, after_seq: int, timeout_s: float) -> tuple[PanelEvent, ...]:
        """Events with `seq > after_seq` in order; blocks up to `timeout_s` for the first
        one and returns `()` on timeout."""
        ...

    def snapshot(self) -> PanelSnapshot:
        """The session state for a (re)joining station."""
        ...

    def asset_bytes(self, asset_id: str) -> bytes:
        """Canonical WAV bytes of a scheduled asset (`KeyError` for any other ID)."""
        ...

    def station_joined(self, rater_id: str, station: str, kind: str) -> None:
        """A `hello` arrived. Raises `PanelRefused("E_UNKNOWN_RATER")` unless the rater,
        station and kind match a seat. The host logs `station_connect` the first time and
        `station_reconnect` afterwards."""
        ...

    def station_left(self, rater_id: str, station: str) -> None:
        """The station's WebSocket closed (`station_disconnect`)."""
        ...

    def asset_ready(self, rater_id: str, station: str, asset_id: str, ok: bool) -> None:
        """A station preloaded an asset (`ok=False`: hash mismatch or fetch failure)."""
        ...

    def clock_synced(self, rater_id: str, station: str, offset_ms: float, rtt_ms: float) -> None:
        """A station's clock-offset estimate after `sync_request`/`sync_reply` (`clock_sync`)."""
        ...

    def report_play(self, report: PlayReport) -> None:
        """A station played an asset (`play` record)."""
        ...

    def submit_rating(self, submission: RatingSubmission) -> RatingAck:
        """Accept or refuse one rating (validated against the slot and the lock time)."""
        ...

    def report_withdrawal(self, rater_id: str, station: str, reason: str) -> None:
        """A rater withdrew: keep every record, mark the batch incomplete (#20)."""
        ...
