"""Bot rater for synthetic panels (#21 implements; #22 uses).

The human station is the static page of `av_generation.panel` (#21). `BotRater` is a
station driven by code that speaks exactly `av_generation.rater_protocol` over a
WebSocket: `hello` with `kind="bot"`, clock sync (`panel.SYNC_BURST` chained probes,
repeated every `panel.SYNC_INTERVAL_MS`), preload with hash check, `played` at the
scheduled onsets (no audio output), and one `rating` per rateable slot drawn from
`seeds.rng_for(seeds.bot_seed_key(run_id, rater_id, "rating", rating_slot_id))`
(`bot_rating`).

It sees only what a station sees: rating-slot IDs, positions, meanings and asset IDs,
never a book ID or method. Fallback injection (#22) is therefore keyed by rating-slot
ID: the driver computes the IDs from the restricted batch config
(`BatchConfig.rating_slot_ids(book_id, atom_id)`) and passes them in
`BotRatingPolicy.force_unacceptable_slots`.

Station rules the bot follows (the same as the station page): a slot is handled at most
once (a re-sent or rejoin `slot` never plays again); an asset is played only if its
hash matched; on a rejoin past the candidate onset the slot is skipped (no audio, no
rating). Fixtures for #22: `p_missing` (no rating), `drop_slots` (the socket drops after
the candidate onset and reconnects mid-slot; that slot's rating is missing) and
`withdraw_at` (the rater withdraws in that slot).

Clocks: `clock` is the bot's own monotonic clock (default `SystemClock`). For
accelerated runs (`ScaledClock`) pass a clock with the same speed as the server's, e.g.
the server's clock object itself; real waits are divided by `clock.speed`.
"""

from __future__ import annotations

import contextlib
import hashlib
import heapq
import itertools
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final

import httpx
from websockets.exceptions import ConnectionClosed, InvalidHandshake
from websockets.sync.client import ClientConnection, connect

from av_generation.clock import Clock, SystemClock
from av_generation.constants import (
    CANDIDATE_ONSET_MS,
    RATING_MAX,
    RATING_MIN,
    RATING_SLOT_MS,
    REFERENCE_ONSET_MS,
)
from av_generation.ids import check_id
from av_generation.panel import ONSET_TOLERANCE_MS, SYNC_BURST, SYNC_INTERVAL_MS
from av_generation.rater_protocol import PROTOCOL_VERSION, WS_PATH, parse_message
from av_generation.seeds import bot_seed_key, rng_for

WITHDRAW_REASON: Final = "rater_request"
DROP_AFTER_MS: Final = 500
"""`drop_slots`: the socket closes this long after the candidate onset."""
DROP_PAUSE_MS: Final = 1_000
"""`drop_slots`: clock time between the drop and the reconnect."""


@dataclass(frozen=True, slots=True)
class BotRatingPolicy:
    """Synthetic rating distribution (#22 proposed: comfort acceptable with p = 0.9;
    association and distinguishability uniform on 1..7)."""

    p_comfort_acceptable: float = 0.9
    force_unacceptable_slots: frozenset[str] = field(default_factory=frozenset)
    """Rating-slot IDs every bot rates comfort `unacceptable` (zero-eligible injection)."""
    p_missing: float = 0.0
    """Probability of submitting no rating in a slot (missing-rating fixtures)."""
    drop_slots: frozenset[str] = field(default_factory=frozenset)
    """Rating-slot IDs in which the bot's socket drops after the candidate onset and
    reconnects mid-slot (no replay; the slot's rating is missing)."""
    withdraw_at: str | None = None
    """Rating-slot ID in which the bot withdraws (`withdraw`, reason `rater_request`)
    once the controls unlock, instead of rating."""
    rt_ms_range: tuple[int, int] = (400, 4_000)
    """Response time after the unlock, drawn uniformly (inclusive), clock ms."""

    def __post_init__(self) -> None:
        for name in ("p_comfort_acceptable", "p_missing"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1], got {value!r}")
        low, high = self.rt_ms_range
        if not 0 <= low <= high:
            raise ValueError(f"rt_ms_range must be 0 <= low <= high, got {self.rt_ms_range!r}")
        object.__setattr__(
            self, "force_unacceptable_slots", frozenset(self.force_unacceptable_slots)
        )
        object.__setattr__(self, "drop_slots", frozenset(self.drop_slots))


@dataclass(frozen=True, slots=True)
class BotRating:
    """One deterministic draw of a bot for one slot (`bot_rating`)."""

    missing: bool
    association: int
    distinguishability: int | None
    comfort: str
    rt_ms: int


def bot_rating(
    run_id: str,
    rater_id: str,
    rating_slot_id: str,
    *,
    ask_distinguishability: bool,
    policy: BotRatingPolicy,
) -> BotRating:
    """The bot's judgments for one slot, from the stream
    `rng_for(bot_seed_key(run_id, rater_id, "rating", rating_slot_id))`.

    Draw order (fixed, so a policy change never shifts the other values): missing
    uniform, association 1..7, distinguishability 1..7 (always drawn; `None` when not
    asked), comfort uniform, response time."""
    rng = rng_for(bot_seed_key(run_id, rater_id, "rating", rating_slot_id))
    u_missing = float(rng.random())
    association = int(rng.integers(RATING_MIN, RATING_MAX + 1))
    distinguishability = int(rng.integers(RATING_MIN, RATING_MAX + 1))
    u_comfort = float(rng.random())
    low, high = policy.rt_ms_range
    rt_ms = int(rng.integers(low, high + 1))
    acceptable = (
        rating_slot_id not in policy.force_unacceptable_slots
        and u_comfort < policy.p_comfort_acceptable
    )
    return BotRating(
        missing=u_missing < policy.p_missing,
        association=association,
        distinguishability=distinguishability if ask_distinguishability else None,
        comfort="acceptable" if acceptable else "unacceptable",
        rt_ms=rt_ms,
    )


@dataclass
class BotRunResult:
    """What one bot saw and did (`BotRater.run`)."""

    rater_id: str
    station: str
    end_reason: str | None = None
    slots: list[str] = field(default_factory=list)
    """Every slot handled, in arrival order."""
    skipped: list[str] = field(default_factory=list)
    """Slots joined past the candidate onset (rejoin): no audio, no rating."""
    plays: list[tuple[str, str, int, int]] = field(default_factory=list)
    """(rating_slot_id, role, scheduled_server_ms, onset_server_ms)."""
    ratings: dict[str, tuple[bool, str | None]] = field(default_factory=dict)
    """rating_slot_id -> (accepted, refusal code)."""
    missing: list[str] = field(default_factory=list)
    """Rateable slots left without a rating (policy, drop or lateness)."""
    assets: dict[str, bool] = field(default_factory=dict)
    """asset_id -> hash check passed."""
    errors: list[tuple[str, str]] = field(default_factory=list)
    """(code, message) of `error` messages and local problems."""
    pauses: list[str] = field(default_factory=list)
    reconnects: int = 0
    withdrawn: bool = False
    offset_ms: float | None = None
    rtt_ms: float | None = None


@dataclass
class _Slot:
    id: str
    message: dict[str, Any]

    @property
    def start(self) -> int:
        return int(self.message["start_server_ms"])


class _Drop(Exception):
    """Simulated network drop (`BotRatingPolicy.drop_slots`)."""


class BotRater:
    """A rater station driven by code (#21 implements; #22 uses)."""

    def __init__(
        self,
        base_url: str,
        *,
        rater_id: str,
        station: str,
        run_id: str,
        policy: BotRatingPolicy,
        clock: Clock | None = None,
        max_reconnects: int = 20,
        open_timeout_s: float = 10.0,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError(f"base_url must be http(s)://host:port, got {base_url!r}")
        check_id(run_id, "run ID")
        self.base_url = base_url.rstrip("/")
        self.ws_url = "ws" + self.base_url[len("http") :] + WS_PATH
        self.rater_id = rater_id
        self.station = station
        self.run_id = run_id
        self.policy = policy
        self.clock: Clock = clock if clock is not None else SystemClock()
        self.speed = float(getattr(self.clock, "speed", 1.0))
        self.max_reconnects = max_reconnects
        self.open_timeout_s = open_timeout_s
        self.result = BotRunResult(rater_id=rater_id, station=station)
        self._buffers: dict[str, int] = {}
        self._handled: dict[str, _Slot] = {}
        self._current: _Slot | None = None
        self._played: set[tuple[str, str]] = set()
        self._offset: float | None = None
        self._actions: list[tuple[int, int, Callable[[ClientConnection], None]]] = []
        self._counter = itertools.count()
        self._sync_seq = 0
        self._burst: tuple[int, float] | None = None
        self._burst_samples: list[tuple[float, float]] = []
        self._last_sync_server_ms: float | None = None
        self._finished = False
        self._http = httpx.Client(base_url=self.base_url, timeout=10.0)

    # -- time ---------------------------------------------------------------

    def _server_now(self) -> float | None:
        return None if self._offset is None else self.clock.now_ms() + self._offset

    def _real_seconds(self, clock_ms: float) -> float:
        return max(0.0, clock_ms) / 1000.0 / self.speed

    def _at(self, server_ms: int, action: Callable[[ClientConnection], None]) -> None:
        heapq.heappush(self._actions, (server_ms, next(self._counter), action))

    # -- main loop ----------------------------------------------------------

    def run(self) -> BotRunResult:
        """Connect, follow the server's slots until `end`, rate each rateable slot.

        Returns what the bot saw and did; reconnects after a lost socket (at most
        `max_reconnects` times) and stops on `end`, withdrawal or `E_UNKNOWN_RATER`."""
        try:
            attempts = 0
            while not self._finished:
                pause_ms = 0.0
                try:
                    with connect(
                        self.ws_url, open_timeout=self.open_timeout_s, max_size=2**16
                    ) as ws:
                        attempts = 0
                        self._session(ws)
                except _Drop:
                    pause_ms = DROP_PAUSE_MS
                except (OSError, ConnectionClosed, InvalidHandshake, TimeoutError) as err:
                    if self._finished:
                        break
                    attempts += 1
                    self.result.errors.append(("E_CONNECTION", type(err).__name__))
                    if attempts > self.max_reconnects:
                        break
                    pause_ms = 100.0 * min(attempts, 10) * self.speed
                if not self._finished:
                    self.result.reconnects += 1
                    self._wait_clock(pause_ms)
        finally:
            self._http.close()
        return self.result

    def _wait_clock(self, clock_ms: float) -> None:
        target = self.clock.now_ms() + clock_ms
        deadline = time.monotonic() + 30.0
        while self.clock.now_ms() < target and time.monotonic() < deadline:
            time.sleep(min(0.01, self._real_seconds(target - self.clock.now_ms()) + 0.0005))

    def _send(self, ws: ClientConnection, message: dict[str, Any]) -> None:
        ws.send(json.dumps(message, separators=(",", ":"), sort_keys=True))

    def _session(self, ws: ClientConnection) -> None:
        self._burst = None
        self._send(
            ws,
            {
                "type": "hello",
                "protocol_version": PROTOCOL_VERSION,
                "station": self.station,
                "rater_id": self.rater_id,
                "kind": "bot",
                "client_ms": float(self.clock.now_ms()),
                "resume_rating_slot_id": None if self._current is None else self._current.id,
            },
        )
        welcomed = False
        while not self._finished:
            now = self._server_now()
            while now is not None and self._actions and self._actions[0][0] <= now:
                _, _, action = heapq.heappop(self._actions)
                action(ws)
                if self._finished:
                    return
                now = self._server_now()
            if (
                welcomed
                and self._burst is None
                and now is not None
                and (
                    self._last_sync_server_ms is None
                    or now - self._last_sync_server_ms >= SYNC_INTERVAL_MS
                )
            ):
                self._start_burst(ws)
            timeout = 0.05
            if now is not None and self._actions:
                timeout = min(timeout, self._real_seconds(self._actions[0][0] - now))
            try:
                text = ws.recv(timeout=max(timeout, 0.0005))
            except TimeoutError:
                continue
            message = parse_message(text, sender="server")
            if message["type"] == "welcome":
                welcomed = True
                if message["state"] == "ended":
                    self._finish(ws, "ended")
                    return
                self._start_burst(ws)
            else:
                self._on_message(ws, message)

    def _finish(self, ws: ClientConnection, reason: str | None) -> None:
        self._finished = True
        self.result.end_reason = reason
        with contextlib.suppress(Exception):
            ws.close()

    # -- clock sync ---------------------------------------------------------

    def _start_burst(self, ws: ClientConnection) -> None:
        self._burst_samples = []
        self._probe(ws, 0)

    def _probe(self, ws: ClientConnection, index: int) -> None:
        sent = float(self.clock.now_ms())
        self._burst = (self._sync_seq + index, sent)
        self._send(ws, {"type": "sync_request", "seq": self._sync_seq + index, "client_ms": sent})

    def _on_sync_reply(self, ws: ClientConnection, message: dict[str, Any]) -> None:
        if self._burst is None or message["seq"] != self._burst[0]:
            return
        received = float(self.clock.now_ms())
        sent = self._burst[1]
        self._burst_samples.append((received - sent, message["server_ms"] - (sent + received) / 2))
        index = message["seq"] - self._sync_seq + 1
        if index < SYNC_BURST:
            self._probe(ws, index)
            return
        rtt, offset = min(self._burst_samples)
        self._offset, self.result.offset_ms, self.result.rtt_ms = offset, offset, rtt
        self._sync_seq += SYNC_BURST
        self._burst = None
        self._last_sync_server_ms = received + offset

    # -- messages -------------------------------------------------------------

    def _on_message(self, ws: ClientConnection, message: dict[str, Any]) -> None:
        kind = message["type"]
        if kind == "sync_reply":
            self._on_sync_reply(ws, message)
        elif kind == "preload":
            for asset in message["assets"]:
                self._preload(ws, asset)
        elif kind == "slot":
            self._on_slot(message)
        elif kind == "rating_ack":
            self.result.ratings[message["rating_slot_id"]] = (message["accepted"], message["code"])
        elif kind == "pause":
            self.result.pauses.append(message["reason"])
        elif kind == "end":
            self._finish(ws, message["reason"])
        elif kind == "error":
            self.result.errors.append((message["code"], message["message"]))
            if message["code"] == "E_UNKNOWN_RATER":
                self._finish(ws, None)

    def _preload(self, ws: ClientConnection, asset: dict[str, Any]) -> None:
        asset_id = asset["asset_id"]
        if asset_id in self._buffers:
            return
        try:
            response = self._http.get(asset["url"])
            data = response.content if response.status_code == 200 else b""
        except httpx.HTTPError:
            data = b""
        ok = len(data) == asset["n_bytes"] and hashlib.sha256(data).hexdigest() == asset_id
        if ok:
            self._buffers[asset_id] = len(data)
        self.result.assets[asset_id] = ok
        self._send(ws, {"type": "asset_ready", "asset_id": asset_id, "ok": ok})

    def _on_slot(self, message: dict[str, Any]) -> None:
        slot_id = message["rating_slot_id"]
        if slot_id in self._handled:
            return  # re-sent or rejoin slot: never handled twice, never replayed
        slot = _Slot(slot_id, message)
        self._handled[slot_id] = slot
        self._current = slot
        self.result.slots.append(slot_id)
        start = slot.start
        if message["rejoin"]:
            now = self._server_now()
            if now is None or now - start > ONSET_TOLERANCE_MS:
                self.result.skipped.append(slot_id)
                if not message["placeholder"]:
                    self.result.missing.append(slot_id)
                return
        if message["placeholder"]:
            return
        self._at(start + CANDIDATE_ONSET_MS, lambda ws: self._play(ws, slot, "candidate"))
        if message["reference"] is not None:
            self._at(start + REFERENCE_ONSET_MS, lambda ws: self._play(ws, slot, "reference"))
        unlock = start + int(message["unlock_offset_ms"])
        if slot_id in self.policy.drop_slots:
            self.result.missing.append(slot_id)
            self._at(start + DROP_AFTER_MS, self._drop)
            return
        if slot_id == self.policy.withdraw_at:
            self._at(unlock, self._withdraw)
            return
        draw = bot_rating(
            self.run_id,
            self.rater_id,
            slot_id,
            ask_distinguishability=bool(message["ask_distinguishability"]),
            policy=self.policy,
        )
        if draw.missing:
            self.result.missing.append(slot_id)
            return
        lock = start + RATING_SLOT_MS
        due = unlock + draw.rt_ms
        if due >= lock - 250:
            due = unlock + max(0, (lock - unlock) // 2)
        self._at(due, lambda ws: self._rate(ws, slot, draw, unlock))

    def _play(self, ws: ClientConnection, slot: _Slot, role: str) -> None:
        key = (slot.id, role)
        if key in self._played:
            return
        asset = slot.message[role]
        if asset is None or asset["asset_id"] not in self._buffers:
            self.result.errors.append(("E_ASSET", f"{slot.id} {role}: asset not ready"))
            return
        self._played.add(key)
        now = self._server_now()
        onset = max(0, round(now if now is not None else 0))
        scheduled = slot.start + int(asset["offset_ms"])
        self.result.plays.append((slot.id, role, scheduled, onset))
        self._send(
            ws,
            {
                "type": "played",
                "rating_slot_id": slot.id,
                "role": role,
                "asset_id": asset["asset_id"],
                "scheduled_server_ms": scheduled,
                "onset_server_ms": onset,
            },
        )

    def _rate(self, ws: ClientConnection, slot: _Slot, draw: BotRating, unlock: int) -> None:
        now = self._server_now()
        if now is None or now >= slot.start + RATING_SLOT_MS:
            self.result.missing.append(slot.id)
            return
        self._send(
            ws,
            {
                "type": "rating",
                "rating_slot_id": slot.id,
                "association": draw.association,
                "distinguishability": draw.distinguishability,
                "comfort": draw.comfort,
                "rt_ms": max(0, round(now) - unlock),
            },
        )

    def _drop(self, ws: ClientConnection) -> None:
        ws.close()
        raise _Drop

    def _withdraw(self, ws: ClientConnection) -> None:
        self.result.withdrawn = True
        self._send(ws, {"type": "withdraw", "reason": WITHDRAW_REASON})
