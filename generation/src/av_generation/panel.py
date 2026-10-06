"""Rater panel server and station page (#21).

`create_panel_app(host, clock=...)` returns the FastAPI app that serves
`rater_protocol.WS_PATH` (one WebSocket per station), `rater_protocol.ASSET_PATH` (WAV
bytes from `host.asset_bytes`) and `rater_protocol.STATION_PAGE` (the static station
page from `STATION_STATIC_DIR`: plain HTML/CSS/JS, no third-party code). It depends only
on `panel_session.PanelSessionHost`, which the orchestrator (#20) implements; the
division of work (who writes which record, rejoin rules) is in `panel_session`. The
server keeps no rating of its own: it relays, checks and answers.

Rules the app enforces before calling the host:

- every frame is validated with `rater_protocol.parse_message(..., sender="station")`;
  anything else gets `error` `E_PROTOCOL` and is not forwarded (no free text, ratings
  only integers 1-7 and a binary comfort choice, frames of at most `MAX_FRAME_BYTES`);
- a station must send `hello` first; a seat that is not in `host.seats()` (rater,
  station and kind) gets `E_UNKNOWN_RATER` and the socket closes. A second `hello` for
  a connected station replaces the old socket (`station_left` for the old one first);
- `sync_request` is answered at once from the server clock. The server also turns each
  burst of `SYNC_BURST` chained probes into one offset estimate for the host
  (`ClockSyncEstimator`, `host.clock_synced`);
- `played` must name a known slot, the slot's asset for that role and the exact
  scheduled time; `rating` is pre-checked with `rating_refusal` and refused as a
  duplicate after an accepted rating of the same station; the host has the last word
  (`host.submit_rating`). A `rating_ack` goes to the rating station only: ratings stay
  private;
- a (re)joining station gets `welcome` from `host.snapshot()`, the pending `preload`
  and the current `slot` with `rejoin=true`, then every later event in order. The
  station plays no asset whose onset has passed (no replay; `generation/docs/rater-panel.md`).

Events of the host (`wait_events`) are broadcast in `seq` order by one task; host
callbacks run in worker threads (`asyncio.to_thread`), so a host that writes logs never
blocks the event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlencode

from fastapi import FastAPI, WebSocket
from fastapi.responses import Response
from starlette.websockets import WebSocketState

from av_generation.clock import Clock
from av_generation.config import RaterSeat
from av_generation.constants import (
    CANDIDATE_ONSET_MS,
    COMFORT_VALUES,
    RATING_MAX,
    RATING_MIN,
    RATING_SLOT_MS,
    REFERENCE_ONSET_MS,
)
from av_generation.panel_session import (
    AssetRef,
    PanelEvent,
    PanelRefused,
    PanelSessionHost,
    PanelSlot,
    PanelSnapshot,
    PlayReport,
    RatingAck,
    RatingSubmission,
)
from av_generation.rater_protocol import (
    ASSET_PATH,
    PROTOCOL_VERSION,
    STATION_PAGE,
    WS_PATH,
    RaterProtocolError,
    message_errors,
    parse_message,
)

STATION_STATIC_DIR: Final = Path(__file__).resolve().parent / "web" / "rater"
"""Static files of the station page: `index.html`, `station.js`, `station.css`."""
STATIC_PATH: Final = "/panel/static/{name}"
STATIC_FILES: Final[Mapping[str, str]] = {
    "station.js": "text/javascript; charset=utf-8",
    "station.css": "text/css; charset=utf-8",
}

SYNC_BURST: Final = 8
"""Probes per clock-sync burst. Probe `i` of burst `b` has `seq = b * SYNC_BURST + i`; a
station sends probe `i + 1` as soon as the reply to probe `i` arrives, so the next
probe's `client_ms` is the arrival time of the previous reply (NTP T4)."""
SYNC_INTERVAL_MS: Final = 60_000
"""Stations repeat a burst at least this often (clock drift stays below ~3 ms at 50 ppm)."""
ONSET_TOLERANCE_MS: Final = 50
"""Proposed onset tolerance: a logged onset within 50 ms of its scheduled time."""
MAX_LATE_START_MS: Final = 1_000
"""A station that gets a (non-rejoin) slot late still plays it if the candidate onset
passed by at most this much; later, or on a rejoin past the onset tolerance, the slot is
shown as a neutral rejoin screen without audio or controls."""
SLOT_LEAD_MS: Final = 500
"""Lead of `slot` events before the slot start: stations schedule audio on receipt, so any
lead above the network and processing delay works (the #20 host also uses 500 ms)."""
MAX_SKEW_MS: Final = 100
"""Proposed limit of the station-to-station onset skew."""
MAX_FRAME_BYTES: Final = 4_096
HELLO_TIMEOUT_S: Final = 10.0
SEND_TIMEOUT_S: Final = 2.0
WAIT_EVENTS_TIMEOUT_S: Final = 0.25

_ASSET_NAME_RE: Final = re.compile(r"([0-9a-f]{64})\.wav")
_SECURITY_HEADERS: Final[Mapping[str, str]] = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self'; media-src 'self'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    ),
}

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure helpers (shared with the host and the bot)


def rating_refusal(slot: PanelSlot, submission: RatingSubmission) -> str | None:
    """The refusal code of a rating for `slot`, or `None` when the slot rules allow it.

    In this order (the same as the #20 host): `E_SLOT_CLOSED` (received at or after the
    lock, 20 s after the slot start), `E_PLACEHOLDER` (invalid candidate: no rating),
    `E_LOCKED` (received before the controls unlock), `E_FIRST_ATOM` (distinguishability
    given on a first-atom slot), `E_PROTOCOL` (a value outside 1-7, a missing
    distinguishability on any other slot, a negative response time). Unknown slots and
    duplicates need session state and are checked by the caller (unknown first,
    duplicates last).
    """
    elapsed = submission.received_ms - slot.start_ms
    if elapsed >= RATING_SLOT_MS:
        return "E_SLOT_CLOSED"
    if slot.placeholder:
        return "E_PLACEHOLDER"
    if elapsed < slot.unlock_offset_ms:
        return "E_LOCKED"
    if slot.first_atom and submission.distinguishability is not None:
        return "E_FIRST_ATOM"
    values: list[int | None] = [submission.association]
    if not slot.first_atom:
        values.append(submission.distinguishability)
    if (
        not all(v is not None and RATING_MIN <= v <= RATING_MAX for v in values)
        or submission.comfort not in COMFORT_VALUES
        or submission.rt_ms < 0
    ):
        return "E_PROTOCOL"
    return None


def asset_url(asset_id: str) -> str:
    """Station URL of an asset (`rater_protocol.ASSET_PATH`)."""
    return ASSET_PATH.format(asset_id=asset_id)


def preload_message(assets: tuple[AssetRef, ...]) -> dict[str, Any]:
    """The `preload` message for a set of assets (deduplicated, in the given order)."""
    seen: dict[str, AssetRef] = {}
    for asset in assets:
        seen.setdefault(asset.asset_id, asset)
    return {
        "type": "preload",
        "assets": [
            {"asset_id": a.asset_id, "url": asset_url(a.asset_id), "n_bytes": a.n_bytes}
            for a in seen.values()
        ],
    }


def slot_message(slot: PanelSlot, *, rejoin: bool = False) -> dict[str, Any]:
    """The `slot` message of a scheduled slot. Placeholders carry no meaning, no audio
    and no controls whatever the host put in the slot."""
    rateable = not slot.placeholder
    candidate = slot.candidate if rateable else None
    reference = slot.reference if rateable else None
    return {
        "type": "slot",
        "rating_slot_id": slot.rating_slot_id,
        "position": slot.position,
        "start_server_ms": slot.start_ms,
        "duration_ms": RATING_SLOT_MS,
        "placeholder": slot.placeholder,
        "meaning": slot.meaning if rateable else None,
        "candidate": (
            None
            if candidate is None
            else {"asset_id": candidate.asset_id, "offset_ms": CANDIDATE_ONSET_MS}
        ),
        "reference": (
            None
            if reference is None or slot.reference_meaning is None
            else {
                "asset_id": reference.asset_id,
                "offset_ms": REFERENCE_ONSET_MS,
                "meaning": slot.reference_meaning,
            }
        ),
        "ask_distinguishability": slot.ask_distinguishability,
        "unlock_offset_ms": slot.unlock_offset_ms,
        "lock_offset_ms": RATING_SLOT_MS,
        "rejoin": rejoin,
    }


def server_message(event: PanelEvent) -> dict[str, Any]:
    """The server message broadcast for one host event."""
    if event.kind == "preload":
        return preload_message(event.assets)
    if event.kind == "slot":
        if event.slot is None:
            raise ValueError(f"slot event {event.seq} has no slot")
        return slot_message(event.slot)
    if event.kind == "pause":
        return {"type": "pause", "reason": event.reason}
    if event.kind == "resume":
        return {"type": "resume"}
    return {"type": "end", "reason": event.reason}


def station_url(base_url: str, station: str, rater_id: str) -> str:
    """The station page URL for one seat (what the operator opens on that station)."""
    return (
        f"{base_url.rstrip('/')}{STATION_PAGE}?{urlencode({'station': station, 'rater': rater_id})}"
    )


def _ascii(text: str, limit: int = 200) -> str:
    return "".join(c if " " <= c <= "~" else "?" for c in text)[:limit]


def _encode(message: Mapping[str, Any]) -> str:
    errors = message_errors(dict(message))
    if errors:  # a server bug, never a station's fault
        raise ValueError(f"invalid server message {message.get('type')!r}: {errors[:3]}")
    return json.dumps(message, separators=(",", ":"), sort_keys=True, ensure_ascii=True)


def _error(code: str, text: str) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": _ascii(text)}


@dataclass
class ClockSyncEstimator:
    """Server-side clock-offset estimate from chained `sync_request` probes.

    With `T1` = probe `i` `client_ms`, `T2 = T3` = the server time of its reply and
    `T4` = probe `i + 1` `client_ms` (sent on arrival of reply `i`): offset
    `(T2 - T1 + T3 - T4) / 2` (server minus station clock) and round trip `T4 - T1`.
    `add()` returns the minimum-round-trip sample of a burst when its last probe
    (`i == SYNC_BURST - 1`) arrives, else `None`.
    """

    burst: int = SYNC_BURST
    _last: tuple[int, float, float] | None = None
    _samples: list[tuple[float, float]] = field(default_factory=list)

    def add(self, seq: int, client_ms: float, server_ms: float) -> tuple[float, float] | None:
        last, self._last = self._last, (seq, client_ms, server_ms)
        if seq % self.burst == 0:
            self._samples.clear()
        if last is None or seq != last[0] + 1 or seq // self.burst != last[0] // self.burst:
            return None
        t1, t2 = last[1], last[2]
        rtt = client_ms - t1
        if rtt >= 0:
            self._samples.append((rtt, t2 - (t1 + client_ms) / 2))
        if seq % self.burst != self.burst - 1 or not self._samples:
            return None
        rtt, offset = min(self._samples)
        self._samples.clear()
        return offset, rtt


# ---------------------------------------------------------------------------
# Server


class _BadFrame(Exception):
    def __init__(self, reason: str, *, fatal: bool = False) -> None:
        super().__init__(reason)
        self.fatal = fatal


@dataclass(eq=False)
class _Station:
    ws: WebSocket
    rater_id: str
    station: str
    kind: str
    after_seq: int
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    sync: ClockSyncEstimator = field(default_factory=ClockSyncEstimator)
    replaced: bool = False
    closed: bool = False


class PanelServer:
    """State of one panel app (one session). Use `create_panel_app`."""

    def __init__(self, host: PanelSessionHost, clock: Clock) -> None:
        self.host = host
        self.clock = clock
        self._stations: dict[str, _Station] = {}
        self._slots: dict[str, PanelSlot] = {}
        self._rated: set[tuple[str, str]] = set()
        self._withdrawn: set[tuple[str, str]] = set()
        self._registry = asyncio.Lock()
        self._dispatch = asyncio.Lock()
        self._pump_task: asyncio.Task[None] | None = None
        self._stopping = False

    # -- lifecycle --------------------------------------------------------

    def ensure_pump(self) -> None:
        """Start the event broadcaster on the running loop (idempotent)."""
        if self._pump_task is None or self._pump_task.done():
            self._stopping = False
            self._pump_task = asyncio.get_running_loop().create_task(self._pump())

    async def aclose(self) -> None:
        """Stop the broadcaster and close every station socket."""
        self._stopping = True
        task, self._pump_task = self._pump_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        for station in list(self._stations.values()):
            await self._close(station, 1001)

    @property
    def connected(self) -> tuple[tuple[str, str], ...]:
        """(station, rater ID) of the connected stations."""
        return tuple(sorted((s.station, s.rater_id) for s in self._stations.values()))

    # -- event broadcast ----------------------------------------------------

    def _observe(self, slot: PanelSlot | None) -> None:
        if slot is not None:
            self._slots[slot.rating_slot_id] = slot

    async def _pump(self) -> None:
        seq = 0
        while not self._stopping:
            try:
                events = await asyncio.to_thread(self.host.wait_events, seq, WAIT_EVENTS_TIMEOUT_S)
            except Exception:  # pragma: no cover - host failure; keep serving
                log.exception("panel host wait_events failed")
                await asyncio.sleep(0.2)
                continue
            if not events:
                continue
            async with self._dispatch:
                for event in sorted(events, key=lambda e: e.seq):
                    if event.seq <= seq:
                        continue
                    seq = event.seq
                    self._observe(event.slot)
                    text = _encode(server_message(event))
                    targets = [s for s in self._stations.values() if event.seq > s.after_seq]
                    for station in targets:
                        station.after_seq = event.seq
                    # concurrently: a stalled station never delays the others
                    await asyncio.gather(*(self._send_text(s, text) for s in targets))

    # -- sending ------------------------------------------------------------

    async def _send_text(self, station: _Station, text: str) -> None:
        if station.closed:
            return
        try:
            async with station.send_lock:
                await asyncio.wait_for(station.ws.send_text(text), SEND_TIMEOUT_S)
        except Exception:
            station.closed = True
            with contextlib.suppress(Exception):
                await station.ws.close(1011)

    async def _send(self, station: _Station, message: Mapping[str, Any]) -> None:
        await self._send_text(station, _encode(message))

    async def _close(self, station: _Station, code: int) -> None:
        station.closed = True
        if station.ws.application_state != WebSocketState.DISCONNECTED:
            with contextlib.suppress(Exception):
                await station.ws.close(code)

    @staticmethod
    async def _reply_and_close(ws: WebSocket, message: Mapping[str, Any], code: int) -> None:
        with contextlib.suppress(Exception):
            await ws.send_text(_encode(message))
            await ws.close(code)

    # -- connection ---------------------------------------------------------

    @staticmethod
    async def _receive(ws: WebSocket) -> str | None:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            return None
        text = message.get("text")
        if text is None:
            raise _BadFrame("binary frames are not part of the protocol")
        if len(text.encode("utf-8")) > MAX_FRAME_BYTES:
            raise _BadFrame(f"frame larger than {MAX_FRAME_BYTES} bytes", fatal=True)
        return str(text)

    async def handle(self, ws: WebSocket) -> None:
        """Serve one station socket until it closes."""
        await ws.accept()
        self.ensure_pump()
        station: _Station | None = None
        try:
            try:
                first = await asyncio.wait_for(self._receive(ws), HELLO_TIMEOUT_S)
                if first is None:
                    return
                hello = parse_message(first, sender="station")
            except (RaterProtocolError, _BadFrame, TimeoutError) as err:
                await self._reply_and_close(ws, _error("E_PROTOCOL", str(err)), 1008)
                return
            if hello["type"] != "hello":
                await self._reply_and_close(
                    ws, _error("E_PROTOCOL", "the first message must be hello"), 1008
                )
                return
            station = await self._join(ws, hello)
            if station is None:
                return
            while not station.closed:
                try:
                    text = await self._receive(ws)
                except _BadFrame as err:
                    await self._send(station, _error("E_PROTOCOL", str(err)))
                    if err.fatal:
                        await self._close(station, 1009)
                        break
                    continue
                if text is None:
                    break
                await self._on_frame(station, text)
        except Exception:  # the socket broke mid-message: treat as a disconnect
            log.debug("station socket error", exc_info=True)
        finally:
            if station is not None:
                await self._leave(station)

    async def _join(self, ws: WebSocket, hello: Mapping[str, Any]) -> _Station | None:
        rater_id, name, kind = hello["rater_id"], hello["station"], hello["kind"]
        if (rater_id, name) in self._withdrawn:
            await self._reply_and_close(ws, {"type": "end", "reason": "withdrawn"}, 1000)
            return None
        seats = await asyncio.to_thread(self.host.seats)
        if RaterSeat(rater_id, name, kind) not in tuple(seats):
            await self._reply_and_close(
                ws, _error("E_UNKNOWN_RATER", f"no seat for {rater_id} at {name}"), 1008
            )
            return None
        async with self._registry:
            old = self._stations.pop(name, None)
            if old is not None:
                old.replaced = True
                await self._close(old, 1000)
                await asyncio.to_thread(self.host.station_left, old.rater_id, old.station)
            try:
                await asyncio.to_thread(self.host.station_joined, rater_id, name, kind)
            except PanelRefused as err:
                await self._reply_and_close(ws, _error(err.code, str(err)), 1008)
                return None
            async with self._dispatch:
                snapshot: PanelSnapshot = await asyncio.to_thread(self.host.snapshot)
                station = _Station(ws, rater_id, name, kind, after_seq=snapshot.seq)
                self._observe(snapshot.slot)
                await self._send(
                    station,
                    {
                        "type": "welcome",
                        "protocol_version": PROTOCOL_VERSION,
                        "session_id": self.host.run_id,
                        "station": name,
                        "server_ms": self.clock.now_ms(),
                        "state": snapshot.state,
                    },
                )
                if snapshot.preload:
                    await self._send(station, preload_message(snapshot.preload))
                if snapshot.state == "slot" and snapshot.slot is not None:
                    await self._send(station, slot_message(snapshot.slot, rejoin=True))
                self._stations[name] = station
        return station

    async def _leave(self, station: _Station) -> None:
        station.closed = True
        async with self._registry:
            if station.replaced or self._stations.get(station.station) is not station:
                return
            del self._stations[station.station]
            await asyncio.to_thread(self.host.station_left, station.rater_id, station.station)

    # -- station messages -----------------------------------------------------

    async def _on_frame(self, station: _Station, text: str) -> None:
        try:
            message = parse_message(text, sender="station")
        except RaterProtocolError as err:
            await self._send(station, _error("E_PROTOCOL", str(err)))
            return
        try:
            await self._dispatch_message(station, message)
        except PanelRefused as err:
            await self._send(station, _error(err.code, str(err)))
        except Exception:  # a host failure must not drop the station
            log.exception("panel host failed on a %s message", message["type"])
            await self._send(station, _error("E_PROTOCOL", "the session could not process it"))

    async def _dispatch_message(self, station: _Station, message: Mapping[str, Any]) -> None:
        kind = message["type"]
        if kind == "sync_request":
            server_ms = self.clock.now_ms()
            await self._send(
                station,
                {
                    "type": "sync_reply",
                    "seq": message["seq"],
                    "client_ms": message["client_ms"],
                    "server_ms": server_ms,
                },
            )
            estimate = station.sync.add(message["seq"], float(message["client_ms"]), server_ms)
            if estimate is not None:
                await asyncio.to_thread(
                    self.host.clock_synced, station.rater_id, station.station, *estimate
                )
        elif kind == "asset_ready":
            await asyncio.to_thread(
                self.host.asset_ready,
                station.rater_id,
                station.station,
                message["asset_id"],
                message["ok"],
            )
        elif kind == "played":
            await self._on_played(station, message)
        elif kind == "rating":
            await self._on_rating(station, message)
        elif kind == "withdraw":
            self._withdrawn.add((station.rater_id, station.station))
            await asyncio.to_thread(
                self.host.report_withdrawal, station.rater_id, station.station, message["reason"]
            )
            await self._send(station, {"type": "end", "reason": "withdrawn"})
            await self._close(station, 1000)
        else:  # hello
            await self._send(station, _error("E_PROTOCOL", "hello was already received"))

    async def _on_played(self, station: _Station, message: Mapping[str, Any]) -> None:
        slot = self._slots.get(message["rating_slot_id"])
        if slot is None:
            await self._send(station, _error("E_UNKNOWN_SLOT", message["rating_slot_id"]))
            return
        role = message["role"]
        asset = slot.candidate if role == "candidate" else slot.reference
        offset = CANDIDATE_ONSET_MS if role == "candidate" else REFERENCE_ONSET_MS
        if (
            slot.placeholder
            or asset is None
            or asset.asset_id != message["asset_id"]
            or message["scheduled_server_ms"] != slot.start_ms + offset
        ):
            await self._send(
                station, _error("E_PROTOCOL", f"played does not match slot {slot.rating_slot_id}")
            )
            return
        report = PlayReport(
            rater_id=station.rater_id,
            station=station.station,
            rating_slot_id=slot.rating_slot_id,
            role=role,
            asset_id=asset.asset_id,
            scheduled_ms=message["scheduled_server_ms"],
            onset_ms=message["onset_server_ms"],
            received_ms=self.clock.now_ms(),
        )
        await asyncio.to_thread(self.host.report_play, report)

    async def _on_rating(self, station: _Station, message: Mapping[str, Any]) -> None:
        received = self.clock.now_ms()
        slot_id = message["rating_slot_id"]
        submission = RatingSubmission(
            rater_id=station.rater_id,
            station=station.station,
            rating_slot_id=slot_id,
            association=message["association"],
            distinguishability=message["distinguishability"],
            comfort=message["comfort"],
            rt_ms=message["rt_ms"],
            received_ms=received,
        )
        slot = self._slots.get(slot_id)
        code = "E_UNKNOWN_SLOT" if slot is None else rating_refusal(slot, submission)
        if code is None and (station.station, slot_id) in self._rated:
            code = "E_DUPLICATE_RATING"
        ack = RatingAck(False, code)
        if code is None:
            try:
                ack = await asyncio.to_thread(self.host.submit_rating, submission)
            except PanelRefused as err:
                ack = RatingAck(False, err.code)
            except Exception:  # the rating is not stored: say so, keep the station
                log.exception("panel host failed on a rating")
                ack = RatingAck(False, "E_PROTOCOL")
        if ack.accepted:
            self._rated.add((station.station, slot_id))
        await self._send(
            station,
            {
                "type": "rating_ack",
                "rating_slot_id": slot_id,
                "accepted": ack.accepted,
                "code": None if ack.accepted else (ack.code or "E_PROTOCOL"),
            },
        )


# ---------------------------------------------------------------------------
# App


def _static(name: str) -> bytes:
    return (STATION_STATIC_DIR / name).read_bytes()


def create_panel_app(host: PanelSessionHost, *, clock: Clock) -> Any:  # noqa: ANN401 - FastAPI app
    """The panel server for one session (#21): station page, assets and WebSockets.

    `clock` must be the host's run clock (the times in slots, plays and ratings).
    `app.state.panel` is the `PanelServer` (connected stations, shutdown)."""
    server = PanelServer(host, clock)
    page = _static("index.html")
    static = {name: (_static(name), media) for name, media in STATIC_FILES.items()}

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        server.ensure_pump()
        yield
        await server.aclose()

    app = FastAPI(
        title="Rater panel", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.panel = server

    @app.get(STATION_PAGE)
    def station_page() -> Response:
        return Response(page, media_type="text/html; charset=utf-8", headers=_SECURITY_HEADERS)

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return Response(status_code=204)

    @app.get(STATIC_PATH)
    def static_file(name: str) -> Response:
        if name not in static:
            return Response(status_code=404)
        body, media = static[name]
        return Response(body, media_type=media, headers=_SECURITY_HEADERS)

    @app.get("/panel/assets/{name}")
    async def asset(name: str) -> Response:
        match = _ASSET_NAME_RE.fullmatch(name)
        if match is None:
            return Response(status_code=404)
        try:
            data = await asyncio.to_thread(host.asset_bytes, match[1])
        except KeyError:
            return Response(status_code=404)
        if hashlib.sha256(data).hexdigest() != match[1]:
            log.error("asset %s: the host returned bytes with another SHA-256", match[1])
            return Response(status_code=500)
        return Response(data, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    @app.websocket(WS_PATH)
    async def station_socket(ws: WebSocket) -> None:
        await server.handle(ws)

    return app
