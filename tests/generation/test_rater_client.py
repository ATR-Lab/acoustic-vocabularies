"""Rater panel server, scripted host and bot rater (#21; browser tests in
`test_rater_station_browser.py`, onset tables in `test_panel_skew.py`)."""

import collections
import dataclasses
import hashlib
import json
import re
import threading
import time
from pathlib import Path

import httpx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from websockets.sync.client import connect

from av_generation.clock import ManualClock, ScaledClock
from av_generation.config import RaterSeat
from av_generation.constants import RATING_SLOT_MS
from av_generation.masking import masking_findings
from av_generation.panel import (
    MAX_FRAME_BYTES,
    SLOT_LEAD_MS,
    STATION_STATIC_DIR,
    SYNC_BURST,
    ClockSyncEstimator,
    create_panel_app,
    preload_message,
    rating_refusal,
    server_message,
    slot_message,
    station_url,
)
from av_generation.panel_demo import (
    DEMO_RUN_ID,
    SCHEDULE_FILE,
    ScriptedPanelHost,
    ScriptStep,
    demo_session,
)
from av_generation.panel_demo import (
    main as panel_demo_main,
)
from av_generation.panel_session import (
    AssetRef,
    PanelEvent,
    PanelRefused,
    PanelSessionHost,
    PanelSlot,
    PlayReport,
    RatingSubmission,
)
from av_generation.panel_skew import main as panel_skew_main
from av_generation.rater import BotRater, BotRatingPolicy, bot_rating
from av_generation.rater_protocol import ASSET_PATH, STATION_PAGE, WS_PATH, message_errors
from av_generation.records import PlayEvent, RatingRecord, TimingEvent, read_records

ROOT = Path(__file__).resolve().parents[2]
BOTS = tuple(RaterSeat(f"R0{i}", f"S{i}", "bot") for i in (1, 2, 3))
H = "a" * 64


# ---------------------------------------------------------------------------
# helpers


class Station:
    """A raw protocol client (websockets, sync) that keeps every frame it receives."""

    def __init__(self, base, station="S1", rater="R01", kind="bot", *, hello=True, resume=None):
        self.ws = connect(base.replace("http", "ws", 1) + WS_PATH, open_timeout=5).__enter__()
        self.frames = []
        if hello:
            self.send(
                {
                    "type": "hello",
                    "protocol_version": 1,
                    "station": station,
                    "rater_id": rater,
                    "kind": kind,
                    "client_ms": 1.0,
                    "resume_rating_slot_id": resume,
                }
            )

    def send(self, message):
        self.ws.send(message if isinstance(message, str | bytes) else json.dumps(message))

    def recv(self, timeout=5.0):
        message = json.loads(self.ws.recv(timeout=timeout))
        assert message_errors(message) == (), message
        self.frames.append(message)
        return message

    def until(self, kind, timeout=5.0):
        deadline = time.monotonic() + timeout
        while True:
            message = self.recv(max(0.01, deadline - time.monotonic()))
            if message["type"] == kind:
                return message

    def drain(self, timeout=0.3):
        try:
            while True:
                self.recv(timeout)
        except TimeoutError:
            pass
        return self.frames

    def closed(self, timeout=3.0):
        try:
            while True:
                self.recv(timeout)
        except TimeoutError:
            return False
        except Exception:
            return True

    def rating(self, slot_id, association=5, distinguishability=3, comfort="acceptable", rt=900):
        self.send(
            {
                "type": "rating",
                "rating_slot_id": slot_id,
                "association": association,
                "distinguishability": distinguishability,
                "comfort": comfort,
                "rt_ms": rt,
            }
        )
        return self.until("rating_ack")

    def close(self):
        self.ws.close()


class SpyHost(ScriptedPanelHost):
    """The scripted host, counting what the panel server forwards."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = collections.Counter()

    def submit_rating(self, submission):
        self.calls["submit_rating"] += 1
        return super().submit_rating(submission)

    def report_play(self, report):
        self.calls["report_play"] += 1
        super().report_play(report)

    def station_joined(self, rater_id, station, kind):
        self.calls["station_joined"] += 1
        super().station_joined(rater_id, station, kind)


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        time.sleep(0.01)


def manual_panel(serve_app, session, *, host_cls=SpyHost):
    clock = ManualClock()
    host = host_cls(session, clock=clock)
    base = serve_app(create_panel_app(host, clock=clock))
    return host, clock, base


def at(host, clock, ms):
    clock.set_ms(ms)
    host.tick()


def slot_of(host, position, round_=1):
    for slot in host.absolute_slots():
        if slot.position == position and slot.rating_slot_id.endswith(f"r{round_}p{position}"):
            return slot
    raise KeyError(position)


def submission(slot, *, at_ms, association=4, distinguishability=4, comfort="acceptable"):
    return RatingSubmission(
        "R01", "S1", slot.rating_slot_id, association, distinguishability, comfort, 700, at_ms
    )


def _slot(first_atom=False, placeholder=False, unlock=2_900, start=10_000):
    asset = AssetRef(H, "b" * 64, 9_600, 19_244)
    return PanelSlot(
        rating_slot_id="DEMO-A-P01.K-a1.r2p5",
        position=5,
        start_ms=start,
        placeholder=placeholder,
        first_atom=first_atom,
        meaning=None if placeholder else "DEMO placeholder meaning for ADD_ONE",
        candidate=None if placeholder else asset,
        reference=None if first_atom else AssetRef("c" * 64, "d" * 64, 9_600, 19_244),
        reference_meaning=None if first_atom else "DEMO placeholder meaning for B",
        unlock_offset_ms=unlock,
    )


# ---------------------------------------------------------------------------
# pure rules


def test_rating_refusal_rules():
    slot = _slot()
    assert rating_refusal(slot, submission(slot, at_ms=12_899)) == "E_LOCKED"
    assert rating_refusal(slot, submission(slot, at_ms=12_900)) is None
    assert rating_refusal(slot, submission(slot, at_ms=29_999)) is None
    assert rating_refusal(slot, submission(slot, at_ms=30_000)) == "E_SLOT_CLOSED"
    assert rating_refusal(slot, submission(slot, at_ms=15_000, distinguishability=None)) == (
        "E_PROTOCOL"
    )
    # order: closed before placeholder, placeholder before locked, locked before values
    assert rating_refusal(_slot(placeholder=True), submission(slot, at_ms=30_000)) == (
        "E_SLOT_CLOSED"
    )
    assert rating_refusal(_slot(placeholder=True), submission(slot, at_ms=10_500)) == (
        "E_PLACEHOLDER"
    )
    assert rating_refusal(slot, submission(slot, at_ms=10_500, association=9)) == "E_LOCKED"
    first = _slot(first_atom=True, unlock=2_000)
    assert rating_refusal(first, submission(first, at_ms=15_000)) == "E_FIRST_ATOM"
    assert rating_refusal(first, submission(first, at_ms=15_000, distinguishability=None)) is None
    invalid = _slot(placeholder=True)
    assert rating_refusal(invalid, submission(invalid, at_ms=15_000)) == "E_PLACEHOLDER"
    assert rating_refusal(slot, submission(slot, at_ms=15_000, association=8)) == "E_PROTOCOL"


@settings(max_examples=200, deadline=None)
@given(
    first=st.booleans(),
    placeholder=st.booleans(),
    unlock=st.integers(0, 20_000),
    elapsed=st.integers(-5_000, 30_000),
    dist=st.one_of(st.none(), st.integers(1, 7)),
)
def test_rating_refusal_accepts_exactly_the_open_window(first, placeholder, unlock, elapsed, dist):
    slot = _slot(first_atom=first, placeholder=placeholder, unlock=unlock)
    code = rating_refusal(
        slot, submission(slot, at_ms=slot.start_ms + elapsed, distinguishability=dist)
    )
    accepted = not placeholder and (dist is None) == first and unlock <= elapsed < RATING_SLOT_MS
    assert (code is None) == accepted


meaning_text = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126), min_size=1, max_size=200
)


@settings(max_examples=100, deadline=None)
@given(
    first=st.booleans(),
    placeholder=st.booleans(),
    meaning=meaning_text,
    ref_meaning=meaning_text,
    unlock=st.integers(0, 20_000),
    rejoin=st.booleans(),
)
def test_slot_messages_are_valid_and_masked(
    first, placeholder, meaning, ref_meaning, unlock, rejoin
):
    slot = dataclasses.replace(
        _slot(first_atom=first, placeholder=placeholder, unlock=unlock),
        meaning=meaning,
        reference_meaning=None if first else ref_meaning,
    )
    message = slot_message(slot, rejoin=rejoin)
    assert message_errors(message) == ()
    assert message["rejoin"] is rejoin
    assert message["ask_distinguishability"] is (not first and not placeholder)
    if placeholder:  # no meaning, no audio, whatever the host put in the slot
        assert message["meaning"] is None and message["candidate"] is None
        assert message["reference"] is None
    else:
        assert message["candidate"] == {"asset_id": H, "offset_ms": 0}
        assert (message["reference"] is None) is first
    keys = json.dumps(sorted(message)) + json.dumps(sorted(message.get("reference") or {}))
    assert masking_findings(keys) == ()
    assert not {"book_id", "slot_id", "method", "seed", "alias"} & set(message)


def test_server_messages_for_every_event_kind():
    asset = AssetRef(H, "b" * 64, 9_600, 19_244)
    preload = server_message(PanelEvent(1, "preload", 0, assets=(asset, asset)))
    assert preload == preload_message((asset,))
    assert preload["assets"] == [
        {"asset_id": H, "url": f"/panel/assets/{H}.wav", "n_bytes": 19_244}
    ]
    assert server_message(PanelEvent(2, "pause", 0, reason="between_atoms")) == {
        "type": "pause",
        "reason": "between_atoms",
    }
    assert server_message(PanelEvent(3, "resume", 0)) == {"type": "resume"}
    assert server_message(PanelEvent(4, "end", 0, reason="aborted")) == {
        "type": "end",
        "reason": "aborted",
    }
    assert server_message(PanelEvent(5, "slot", 0, slot=_slot()))["rejoin"] is False
    with pytest.raises(ValueError):
        server_message(PanelEvent(6, "slot", 0))
    for seq, message in enumerate((preload,)):
        assert message_errors(message) == (), seq
    assert station_url("http://10.0.0.5:8765/", "S2", "R02") == (
        "http://10.0.0.5:8765/panel/station?station=S2&rater=R02"
    )


@settings(max_examples=100, deadline=None)
@given(
    offset=st.floats(-1e6, 1e6),
    start=st.floats(0, 1e6),
    delays=st.lists(
        st.tuples(st.floats(0, 50), st.floats(0, 50)), min_size=SYNC_BURST, max_size=SYNC_BURST
    ),
    burst=st.integers(0, 5),
)
def test_clock_sync_estimator_bounds_the_offset_error(offset, start, delays, burst):
    """Chained probes: T4 of probe i is the client_ms of probe i + 1. The estimate is
    within half the round trip of the true offset (server = client + offset)."""
    estimator = ClockSyncEstimator()
    client = start
    results = []
    for i, (up, down) in enumerate(delays):
        server = client + offset + up
        results.append(estimator.add(burst * SYNC_BURST + i, client, server))
        client = client + up + down
    assert all(r is None for r in results[:-1])
    estimate, rtt = results[-1]
    assert rtt >= 0
    assert abs(estimate - offset) <= rtt / 2 + 1e-6
    # a burst whose last probe never arrives gives no estimate; the next burst does
    partial = ClockSyncEstimator()
    assert all(partial.add(seq, float(seq), float(seq)) is None for seq in range(SYNC_BURST - 1))
    assert [partial.add(seq, 2.0 * seq, 2.0 * seq) for seq in range(8, 16)][-1] == (-1.0, 2.0)


# ---------------------------------------------------------------------------
# panel app: page, assets, protocol


def test_station_page_static_files_and_masking(serve_app):
    host = ScriptedPanelHost(demo_session(seats=BOTS, rounds=1), clock=ManualClock())
    base = serve_app(create_panel_app(host, clock=ManualClock()))
    page = httpx.get(base + STATION_PAGE, timeout=5)
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "default-src 'none'" in csp
    scripts = re.findall(r"<script[^>]*src=\"([^\"]+)\"", page.text)
    assert scripts == ["/panel/static/station.js"]
    assert "<script>" not in page.text and "http" not in page.text.replace("http-equiv", "")
    for name in ("station.js", "station.css"):
        response = httpx.get(f"{base}/panel/static/{name}", timeout=5)
        assert response.status_code == 200
        assert response.content == (STATION_STATIC_DIR / name).read_bytes()
    assert httpx.get(f"{base}/panel/static/index.py", timeout=5).status_code == 404
    assert httpx.get(f"{base}/favicon.ico", timeout=5).status_code == 204
    assert httpx.get(f"{base}/docs", timeout=5).status_code == 404
    # Masking: the page shows no method, book or designer word. The only finding in the
    # static files is the protocol's station kind value ("human") in station.js.
    visible = re.sub(r"<[^>]+>", " ", page.text)
    assert masking_findings(visible) == ()
    findings = {
        name: masking_findings((STATION_STATIC_DIR / name).read_text(encoding="utf-8"))
        for name in ("index.html", "station.js", "station.css")
    }
    assert findings == {
        "index.html": (),
        "station.js": ("method word 'human'",),
        "station.css": (),
    }
    assert "DEMO-BK" not in page.text


def test_assets_are_served_by_hash_only(serve_app):
    session = demo_session(seats=BOTS, rounds=1, positions=[1])
    host = ScriptedPanelHost(session, clock=ManualClock())
    base = serve_app(create_panel_app(host, clock=ManualClock()))
    asset_id, data = next(iter(session.assets.items()))
    response = httpx.get(base + ASSET_PATH.format(asset_id=asset_id), timeout=5)
    assert response.status_code == 200 and response.headers["content-type"] == "audio/wav"
    assert hashlib.sha256(response.content).hexdigest() == asset_id
    assert response.content == data
    assert httpx.get(base + ASSET_PATH.format(asset_id="0" * 64), timeout=5).status_code == 404
    assert httpx.get(f"{base}/panel/assets/{asset_id}.mp3", timeout=5).status_code == 404
    assert httpx.get(f"{base}/panel/assets/{asset_id.upper()}.wav", timeout=5).status_code == 404

    class Corrupt(ScriptedPanelHost):
        def asset_bytes(self, asset_id):
            return super().asset_bytes(asset_id) + b"\0\0"

    bad = serve_app(create_panel_app(Corrupt(session, clock=ManualClock()), clock=ManualClock()))
    assert httpx.get(bad + ASSET_PATH.format(asset_id=asset_id), timeout=5).status_code == 500


def test_hello_first_and_unknown_seats_are_refused(serve_app):
    host, _, base = manual_panel(serve_app, demo_session(seats=BOTS, rounds=1, positions=[1]))
    first = Station(base, hello=False)
    first.send({"type": "withdraw", "reason": "other"})
    assert first.recv()["code"] == "E_PROTOCOL"
    assert first.closed()
    garbage = Station(base, hello=False)
    garbage.send("not json")
    assert garbage.recv()["code"] == "E_PROTOCOL"
    for station, rater, kind in (
        ("S1", "R09", "bot"),
        ("S1", "R01", "human"),
        ("S2", "R01", "bot"),
    ):
        refused = Station(base, station, rater, kind)
        error = refused.recv()
        assert (error["type"], error["code"]) == ("error", "E_UNKNOWN_RATER")
        assert refused.closed()
    assert host.calls["station_joined"] == 0
    assert host.timing == []


@pytest.mark.parametrize(
    "change",
    [
        {"association": 8},
        {"association": 0},
        {"association": 4.5},
        {"distinguishability": "3"},
        {"comfort": "fine"},
        {"comment": "too loud"},
        {"rt_ms": -5},
    ],
)
def test_invalid_ratings_and_free_text_never_reach_the_host(serve_app, change):
    host, clock, base = manual_panel(serve_app, demo_session(seats=BOTS, rounds=1, positions=[1]))
    host.begin(0)
    station = Station(base)
    station.until("welcome")
    at(host, clock, 5_000 + 4_000)
    slot = station.until("slot")
    message = {
        "type": "rating",
        "rating_slot_id": slot["rating_slot_id"],
        "association": 5,
        "distinguishability": 3,
        "comfort": "acceptable",
        "rt_ms": 1_000,
        **change,
    }
    station.send(message)
    error = station.until("error")
    assert error["code"] == "E_PROTOCOL"
    assert host.calls["submit_rating"] == 0
    # the socket stays usable for a valid rating afterwards
    assert station.rating(slot["rating_slot_id"])["accepted"] is True
    assert host.calls["submit_rating"] == 1


def test_binary_and_oversized_frames_are_refused(serve_app):
    host, _, base = manual_panel(serve_app, demo_session(seats=BOTS, rounds=1, positions=[1]))
    station = Station(base)
    station.until("welcome")
    station.send(b"\x00\x01")
    assert station.until("error")["code"] == "E_PROTOCOL"
    station.send(json.dumps({"type": "withdraw", "reason": "x" * MAX_FRAME_BYTES}))
    assert station.until("error")["code"] == "E_PROTOCOL"
    assert station.closed()
    assert host.calls["submit_rating"] == 0 and host.withdrawals == []


def test_join_snapshot_sync_and_replaced_socket(serve_app):
    session = demo_session(seats=BOTS, rounds=1, positions=[1, 2])
    host, clock, base = manual_panel(serve_app, session)
    host.begin(0)
    at(host, clock, 5_000 + 600)  # slot 1 running, its candidate onset passed
    s1 = Station(base)
    welcome = s1.recv()
    assert welcome["type"] == "welcome" and welcome["state"] == "slot"
    assert welcome["session_id"] == DEMO_RUN_ID and welcome["station"] == "S1"
    preload = s1.recv()
    assert preload["type"] == "preload" and len(preload["assets"]) >= 3
    rejoin = s1.recv()
    assert rejoin["type"] == "slot" and rejoin["rejoin"] is True
    assert rejoin["rating_slot_id"] == slot_of(host, 1).rating_slot_id
    # a full sync burst gives one clock_sync timing event with the server's estimate
    for i in range(SYNC_BURST):
        s1.send({"type": "sync_request", "seq": i, "client_ms": 100.0 + 2 * i})
        reply = s1.until("sync_reply")
        assert (reply["seq"], reply["client_ms"], reply["server_ms"]) == (i, 100.0 + 2 * i, 5_600)
    wait_for(lambda: any(t.event == "clock_sync" for t in host.timing))
    sync = [t for t in host.timing if t.event == "clock_sync"]
    assert len(sync) == 1 and sync[0].station == "S1" and "rtt_ms=2.0" in sync[0].detail
    # a second hello for S1 replaces the first socket (disconnect, then reconnect)
    s1b = Station(base)
    assert s1b.until("welcome")["state"] == "slot"
    assert s1.closed()
    wait_for(
        lambda: [t.event for t in host.timing][-2:] == ["station_disconnect", "station_reconnect"]
    )
    at(host, clock, 25_000)
    finals = [r for r in host.ratings if r.position == 1 and r.station == "S1"]
    assert finals and finals[0].missing and finals[0].reconnected


def test_rating_window_privacy_and_records(serve_app):
    session = demo_session(seats=BOTS, rounds=1, positions=[1, 2, 5], placeholders=[(1, 5)])
    host, clock, base = manual_panel(serve_app, session)
    host.begin(0)
    s1, s2 = Station(base, "S1", "R01"), Station(base, "S2", "R02")
    s1.until("welcome")
    s2.until("welcome")
    p1, p2, p5 = (slot_of(host, p) for p in (1, 2, 5))
    at(host, clock, p1.start_ms - SLOT_LEAD_MS)
    assert s1.until("slot")["rating_slot_id"] == p1.rating_slot_id
    assert s2.until("slot")["rejoin"] is False
    at(host, clock, p1.start_ms + 500)
    assert s1.rating(p1.rating_slot_id)["code"] == "E_LOCKED"
    at(host, clock, p1.start_ms + p1.unlock_offset_ms)
    ack = s1.rating(p1.rating_slot_id, association=6, distinguishability=2, comfort="unacceptable")
    assert (ack["accepted"], ack["code"]) == (True, None)
    assert s1.rating(p1.rating_slot_id)["code"] == "E_DUPLICATE_RATING"
    assert s1.rating(p2.rating_slot_id)["code"] == "E_UNKNOWN_SLOT"
    assert s2.rating(p1.rating_slot_id, distinguishability=None)["code"] == "E_PROTOCOL"
    at(host, clock, p1.start_ms + RATING_SLOT_MS)
    assert s2.rating(p1.rating_slot_id)["code"] == "E_SLOT_CLOSED"
    at(host, clock, p5.start_ms + 3_000)
    placeholder = s1.until("slot")
    while placeholder["rating_slot_id"] != p5.rating_slot_id:
        placeholder = s1.until("slot")
    assert placeholder["placeholder"] is True and placeholder["meaning"] is None
    assert s1.rating(p5.rating_slot_id)["code"] == "E_PLACEHOLDER"
    at(host, clock, p5.start_ms + RATING_SLOT_MS + 1_000)
    assert s1.until("end")["reason"] == "appointment_complete"
    s2.until("end")
    # privacy: every rating_ack a station received answers its own rating
    s2_acks = [f for f in s2.drain() if f["type"] == "rating_ack"]
    assert len(s2_acks) == 2 and {a["code"] for a in s2_acks} == {"E_PROTOCOL", "E_SLOT_CLOSED"}
    for frame in s1.frames + s2.frames:
        text = json.dumps(frame)
        assert masking_findings(text) == ()
        assert "DEMO-BK" not in text and ".r1s" not in text
    # records: one per seat and slot; submitted, missing or placeholder
    by_key = {(r.station, r.position): r for r in host.ratings}
    assert len(host.ratings) == 3 * 3
    r = by_key[("S1", 1)]
    assert (r.association, r.distinguishability, r.comfort, r.missing) == (
        6,
        2,
        "unacceptable",
        False,
    )
    assert r.rt_ms == 900 and r.unlock_ms == p1.unlock_offset_ms and r.slot_start_ms == p1.start_ms
    assert by_key[("S2", 1)].missing and by_key[("S3", 2)].missing
    assert all(
        by_key[(s, 5)].placeholder and not by_key[(s, 5)].missing for s in ("S1", "S2", "S3")
    )
    for record in host.ratings:
        record.check()


def test_first_atom_stores_distinguishability_by_rule(serve_app):
    session = demo_session(seats=BOTS, rounds=1, positions=[1], atom_index=0)
    host, clock, base = manual_panel(serve_app, session)
    host.begin(0)
    s1 = Station(base)
    s1.until("welcome")
    p1 = slot_of(host, 1)
    assert p1.first_atom and p1.reference is None and p1.unlock_offset_ms == 2_000
    at(host, clock, p1.start_ms)
    message = s1.until("slot")
    assert message["ask_distinguishability"] is False and message["reference"] is None
    at(host, clock, p1.start_ms + 2_000)
    assert s1.rating(p1.rating_slot_id, distinguishability=5)["code"] == "E_FIRST_ATOM"
    assert s1.rating(p1.rating_slot_id, distinguishability=None)["accepted"] is True
    at(host, clock, p1.start_ms + RATING_SLOT_MS)
    record = next(r for r in host.ratings if r.station == "S1")
    assert (record.distinguishability, record.distinguishability_by_rule) == (4, True)
    other = next(r for r in host.ratings if r.station == "S2")
    assert (
        other.missing and other.distinguishability is None and not other.distinguishability_by_rule
    )


def test_played_reports_are_checked_against_the_slot(serve_app):
    session = demo_session(seats=BOTS, rounds=1, positions=[1])
    host, clock, base = manual_panel(serve_app, session)
    host.begin(0)
    s1 = Station(base)
    s1.until("welcome")
    at(host, clock, 5_000)
    slot = s1.until("slot")
    candidate = slot["candidate"]["asset_id"]
    played = {
        "type": "played",
        "rating_slot_id": slot["rating_slot_id"],
        "role": "candidate",
        "asset_id": candidate,
        "scheduled_server_ms": slot["start_server_ms"],
        "onset_server_ms": slot["start_server_ms"] + 4,
    }
    s1.send({**played, "asset_id": slot["reference"]["asset_id"]})
    assert s1.until("error")["code"] == "E_PROTOCOL"
    s1.send({**played, "scheduled_server_ms": slot["start_server_ms"] + 1})
    assert s1.until("error")["code"] == "E_PROTOCOL"
    s1.send({**played, "rating_slot_id": "DEMO-A-P01.K-a1.r4p9"})
    assert s1.until("error")["code"] == "E_UNKNOWN_SLOT"
    s1.send(played)
    wait_for(lambda: len(host.plays) == 1)
    play = host.plays[0]
    assert (play.context, play.station, play.actor_id, play.onset_ms) == (
        "rating_candidate",
        "S1",
        "R01",
        slot["start_server_ms"] + 4,
    )
    assert play.asset_id == candidate and play.slot_id is not None
    assert host.calls["report_play"] == 1


def test_host_failures_keep_the_station_connected(serve_app):
    class Failing(SpyHost):
        def report_play(self, report):
            raise RuntimeError("disk full")

        def submit_rating(self, submission):
            raise RuntimeError("disk full")

        def asset_ready(self, rater_id, station, asset_id, ok):
            raise PanelRefused("E_UNKNOWN_SLOT", "not scheduled")

    host, clock, base = manual_panel(
        serve_app, demo_session(seats=BOTS, rounds=1, positions=[1]), host_cls=Failing
    )
    host.begin(0)
    s1 = Station(base)
    s1.until("welcome")
    at(host, clock, 5_000 + 4_000)
    slot = s1.until("slot")
    s1.send({"type": "asset_ready", "asset_id": H, "ok": True})
    assert s1.until("error")["code"] == "E_UNKNOWN_SLOT"
    s1.send(
        {
            "type": "played",
            "rating_slot_id": slot["rating_slot_id"],
            "role": "candidate",
            "asset_id": slot["candidate"]["asset_id"],
            "scheduled_server_ms": slot["start_server_ms"],
            "onset_server_ms": slot["start_server_ms"],
        }
    )
    assert s1.until("error")["code"] == "E_PROTOCOL"
    ack = s1.rating(slot["rating_slot_id"])
    assert (ack["accepted"], ack["code"]) == (False, "E_PROTOCOL")
    s1.send({"type": "sync_request", "seq": 0, "client_ms": 1.0})
    assert s1.until("sync_reply")["seq"] == 0


def test_withdrawal_is_reported_and_final(serve_app):
    host, _, base = manual_panel(serve_app, demo_session(seats=BOTS, rounds=1, positions=[1]))
    s3 = Station(base, "S3", "R03")
    s3.until("welcome")
    s3.send({"type": "withdraw", "reason": "discomfort"})
    assert s3.until("end")["reason"] == "withdrawn"
    assert s3.closed()
    wait_for(lambda: host.withdrawals == [("R03", "S3", "discomfort")])
    again = Station(base, "S3", "R03")
    assert again.recv() == {"type": "end", "reason": "withdrawn"}
    events = [t.event for t in host.timing]
    assert "rater_withdrawal" in events and events.count("station_connect") == 1


def test_operator_events_reach_every_station_in_order(serve_app):
    host, clock, base = manual_panel(serve_app, demo_session(seats=BOTS, rounds=1, positions=[1]))
    host.begin(0)
    stations = [Station(base, s.station, s.rater_id) for s in BOTS]
    for station in stations:
        station.until("welcome")
    at(host, clock, 5_000)
    host.republish(slot_of(host, 1).rating_slot_id)
    host.operator("pause", "operator")
    host.operator("resume")
    host.operator("pause", "between_atoms")
    assert host.snapshot().state == "between_atoms"
    host.operator("end", "aborted")
    assert host.snapshot().state == "ended"
    for station in stations:
        station.until("end")
        kinds = [f["type"] for f in station.frames if f["type"] not in ("welcome", "preload")]
        assert kinds == ["slot", "slot", "pause", "resume", "pause", "end"]


# ---------------------------------------------------------------------------
# scripted host on its own


def test_scripted_host_satisfies_the_contract_and_logs(tmp_path):
    clock = ManualClock()
    session = demo_session(seats=BOTS, rounds=1, positions=[1, 3], placeholders=[(1, 3)])
    host = ScriptedPanelHost(session, clock=clock, run_dir=tmp_path / DEMO_RUN_ID)
    assert isinstance(host, PanelSessionHost)
    assert host.snapshot().state == "waiting" and host.wait_events(0, 0.01) == ()
    with pytest.raises(RuntimeError):
        host.absolute_slots()
    clock.set_ms(1_000)
    host.begin()
    with pytest.raises(RuntimeError):
        host.begin()
    assert host.anchor_ms == 1_000
    first = slot_of(host, 1)
    assert first.start_ms == 6_000
    schedule = json.loads((tmp_path / DEMO_RUN_ID / SCHEDULE_FILE).read_text(encoding="utf-8"))
    assert [s["start_ms"] for s in schedule["slots"]] == [6_000, 26_000]
    assert schedule["slots"][1]["candidate"] is None
    host.station_joined("R01", "S1", "bot")
    host.asset_ready("R01", "S1", H, True)
    host.clock_synced("R01", "S1", -1.5, 0.8)
    at(host, clock, 6_000)
    events = host.wait_events(0, 0.1)
    assert [e.kind for e in events] == ["preload", "slot"]
    assert host.snapshot().slot == first
    host.report_play(
        PlayReport(
            "R01",
            "S1",
            first.rating_slot_id,
            "candidate",
            first.candidate.asset_id,
            first.start_ms,
            first.start_ms + 3,
            first.start_ms + 9,
        )
    )
    host.station_left("R01", "S1")
    host.station_joined("R01", "S1", "bot")
    at(host, clock, 26_000 + RATING_SLOT_MS + 1_000)
    assert host.ended
    logs = tmp_path / DEMO_RUN_ID / "logs"
    ratings = read_records(logs / "ratings.jsonl", RatingRecord)
    plays = read_records(logs / "plays.jsonl", PlayEvent)
    timing = read_records(logs / "timing.jsonl", TimingEvent)
    assert len(ratings) == 6 and len(plays) == 1 and plays[0].onset_ms == first.start_ms + 3
    s1 = next(r for r in ratings if r.station == "S1" and r.position == 1)
    assert s1.missing and s1.reconnected and s1.candidate_onset_ms == 3
    assert [t.event for t in timing] == [
        "station_connect",
        "asset_ready",
        "clock_sync",
        "station_disconnect",
        "station_reconnect",
    ]
    assert all(r.rater_kind == "bot" for r in ratings)


def test_demo_session_shape_is_deterministic():
    session = demo_session(rounds=4, placeholders=[(2, 4)])
    assert session == demo_session(rounds=4, placeholders=[(2, 4)])
    assert len(session.slots) == 36
    ids = [s.panel.rating_slot_id for s in session.slots]
    assert ids[0] == "DEMO-A-P01.K-a1.r1p1" and ids[-1] == "DEMO-A-P01.K-a1.r4p9"
    starts = [s.panel.start_ms for s in session.slots]
    assert starts == [5_000 + 20_000 * i for i in range(36)]
    assert [s.book_id for s in session.slots[:9]] == [
        b for b in ("DEMO-BK-M2RW", "DEMO-BK-H9TC", "DEMO-BK-7QX4") for _ in range(3)
    ]
    placeholder = session.slots[9 + 3]
    assert placeholder.panel.placeholder and placeholder.panel.candidate is None
    for asset_id, data in session.assets.items():
        assert hashlib.sha256(data).hexdigest() == asset_id
    preloads = [s for s in session.steps if s.kind == "preload"]
    assert len(preloads) == 4
    for round_, step in enumerate(preloads):
        wanted = {
            a.asset_id
            for s in session.slots[9 * round_ : 9 * round_ + 9]
            for a in (s.panel.candidate, s.panel.reference)
            if a is not None
        }
        assert {a.asset_id for a in step.assets} == wanted
    slot_steps = [s for s in session.steps if s.kind == "slot"]
    assert all(s.at_ms == session.slots[s.slot].panel.start_ms - SLOT_LEAD_MS for s in slot_steps)
    assert session.steps[-1] == ScriptStep(
        5_000 + 36 * 20_000 + 500, "end", reason="appointment_complete"
    )
    first_atom = demo_session(atom_index=0, positions=[1, 2])
    assert all(s.panel.first_atom and s.panel.reference is None for s in first_atom.slots)
    with pytest.raises(ValueError):
        demo_session(positions=[10])


# ---------------------------------------------------------------------------
# bot raters over the real server (accelerated clock)


def run_bots(serve_app, session, policies, *, speed=25.0, republish=None):
    clock = ScaledClock(speed)
    host = ScriptedPanelHost(session, clock=clock)
    base = serve_app(create_panel_app(host, clock=clock))
    bots = [
        BotRater(
            base,
            rater_id=s.rater_id,
            station=s.station,
            run_id=session.run_id,
            policy=policies.get(s.station, BotRatingPolicy()),
            clock=clock,
        )
        for s in session.seats
    ]
    results = {}
    threads = [
        threading.Thread(target=lambda b=b: results.__setitem__(b.station, b.run()), daemon=True)
        for b in bots
    ]
    for thread in threads:
        thread.start()
    wait_for(lambda: sum(t.event == "clock_sync" for t in host.timing) >= len(bots))
    host.begin()
    host.start()
    if republish is not None:
        wait_for(
            lambda: republish in {e.slot.rating_slot_id for e in host.wait_events(0, 0) if e.slot}
        )
        host.republish(republish)
    assert host.wait_ended(120)
    for thread in threads:
        thread.join(30)
    host.stop()
    return host, results


def test_three_bots_rate_one_round(serve_app):
    session = demo_session(seats=BOTS, rounds=1, placeholders=[(1, 5)])
    ids = [s.panel.rating_slot_id for s in session.slots]
    drop, forced = ids[2], ids[0]
    policies = {
        "S1": BotRatingPolicy(force_unacceptable_slots=frozenset({forced})),
        "S2": BotRatingPolicy(
            force_unacceptable_slots=frozenset({forced}), drop_slots=frozenset({drop})
        ),
        "S3": BotRatingPolicy(force_unacceptable_slots=frozenset({forced}), p_missing=0.3),
    }
    host, results = run_bots(serve_app, session, policies, republish=ids[1])
    assert len(host.ratings) == 27
    per_seat = collections.Counter(r.station for r in host.ratings)
    assert per_seat == {"S1": 9, "S2": 9, "S3": 9}
    records = {(r.station, r.rating_slot_id): r for r in host.ratings}
    assert all(records[(s, forced)].comfort == "unacceptable" for s in ("S1", "S2"))
    dropped = records[("S2", drop)]
    assert dropped.missing and dropped.reconnected
    assert all(records[(s, ids[4])].placeholder for s in ("S1", "S2", "S3"))
    # S3's missing ratings are exactly the bot's own draws
    s3_missing = {r.rating_slot_id for r in host.ratings if r.station == "S3" and r.missing}
    assert s3_missing == set(results["S3"].missing)
    expected_s3 = {
        i
        for i in ids
        if i != ids[4]
        and bot_rating(
            session.run_id, "R03", i, ask_distinguishability=True, policy=policies["S3"]
        ).missing
    }
    assert s3_missing == expected_s3
    # no asset is played twice (also not after the republished slot or the reconnect)
    plays = collections.Counter((p.station, p.rating_slot_id, p.context) for p in host.plays)
    assert set(plays.values()) == {1}
    assert len(plays) == 3 * 8 * 2  # 8 rateable slots, candidate + reference, 3 stations
    assert not any(p.rating_slot_id == ids[4] for p in host.plays)
    for result in results.values():
        assert result.end_reason == "appointment_complete" and result.errors == []
        assert len(result.slots) == 9 and len(set(result.slots)) == 9
    assert results["S2"].reconnects == 1
    accepted = {(s, i) for s, r in results.items() for i, (ok, _) in r.ratings.items() if ok}
    stored = {(r.station, r.rating_slot_id) for r in host.ratings if r.association is not None}
    assert accepted == stored
    for record in host.ratings:
        record.check()
        if not record.placeholder and not record.missing:
            assert record.candidate_onset_ms is not None and record.reference_onset_ms is not None


def test_first_atom_round_with_a_withdrawal(serve_app):
    session = demo_session(seats=BOTS, rounds=1, positions=[1, 2, 3], atom_index=0)
    ids = [s.panel.rating_slot_id for s in session.slots]
    host, results = run_bots(serve_app, session, {"S3": BotRatingPolicy(withdraw_at=ids[1])})
    assert host.withdrawals == [("R03", "S3", "rater_request")]
    assert results["S3"].withdrawn and results["S3"].end_reason == "withdrawn"
    s3 = {r.rating_slot_id: r for r in host.ratings if r.station == "S3"}
    assert not s3[ids[0]].missing and s3[ids[1]].missing and s3[ids[2]].missing
    rated = [r for r in host.ratings if not r.missing]
    assert rated and all(r.distinguishability == 4 and r.distinguishability_by_rule for r in rated)
    assert not any(p.context == "rating_reference" for p in host.plays)
    assert len(host.ratings) == 9


def test_bot_rating_draws_are_deterministic_and_policies_checked():
    policy = BotRatingPolicy()
    a = bot_rating(
        "DEMO-run-1", "R01", "DEMO-A-P01.K-a1.r1p1", ask_distinguishability=True, policy=policy
    )
    assert a == bot_rating(
        "DEMO-run-1", "R01", "DEMO-A-P01.K-a1.r1p1", ask_distinguishability=True, policy=policy
    )
    b = bot_rating(
        "DEMO-run-1", "R02", "DEMO-A-P01.K-a1.r1p1", ask_distinguishability=True, policy=policy
    )
    c = bot_rating(
        "DEMO-run-1", "R01", "DEMO-A-P01.K-a1.r1p1", ask_distinguishability=False, policy=policy
    )
    assert (c.association, c.comfort, c.distinguishability) == (a.association, a.comfort, None)
    assert a != b or a.rt_ms != b.rt_ms
    draws = [
        bot_rating(
            "DEMO-run-1",
            "R01",
            f"DEMO-A-P01.K-a1.r{r}p{p}",
            ask_distinguishability=True,
            policy=policy,
        )
        for r in range(1, 5)
        for p in range(1, 10)
    ]
    assert {d.association for d in draws} <= set(range(1, 8)) and not any(d.missing for d in draws)
    assert all(400 <= d.rt_ms <= 4_000 for d in draws)
    forced = BotRatingPolicy(force_unacceptable_slots={"DEMO-A-P01.K-a1.r1p1"}, p_missing=1.0)
    d = bot_rating(
        "DEMO-run-1", "R01", "DEMO-A-P01.K-a1.r1p1", ask_distinguishability=True, policy=forced
    )
    assert d.comfort == "unacceptable" and d.missing
    assert isinstance(forced.force_unacceptable_slots, frozenset)
    for bad in ({"p_missing": 1.5}, {"p_comfort_acceptable": -0.1}, {"rt_ms_range": (5, 1)}):
        with pytest.raises(ValueError):
            BotRatingPolicy(**bad)
    with pytest.raises(ValueError):
        BotRater("ws://127.0.0.1:1", rater_id="R01", station="S1", run_id="DEMO-x", policy=policy)


def test_bot_with_an_unknown_seat_stops(serve_app):
    host = ScriptedPanelHost(demo_session(seats=BOTS, rounds=1, positions=[1]), clock=ManualClock())
    base = serve_app(create_panel_app(host, clock=ManualClock()))
    result = BotRater(
        base, rater_id="R09", station="S9", run_id=DEMO_RUN_ID, policy=BotRatingPolicy()
    ).run()
    assert result.errors and result.errors[0][0] == "E_UNKNOWN_RATER"
    assert result.end_reason is None and result.slots == []


# ---------------------------------------------------------------------------
# command lines


def test_demo_and_logged_onset_commands(tmp_path, capsys):
    out = tmp_path / "panel"
    code = panel_demo_main(
        [
            "--bots",
            "--speed",
            "40",
            "--rounds",
            "1",
            "--start-delay-s",
            "1",
            "--port",
            "0",
            "--placeholder",
            "1:9",
            "--out-dir",
            str(out),
            "--run-id",
            "DEMO-panel-cli",
        ]
    )
    assert code == 0
    run = out / "DEMO-panel-cli"
    ratings = read_records(run / "logs" / "ratings.jsonl", RatingRecord)
    assert len(ratings) == 27 and sum(r.placeholder for r in ratings) == 3
    printed = capsys.readouterr().out
    assert "S1 (R01): http://127.0.0.1:" in printed and "27 rating records" in printed
    code = panel_skew_main(
        [
            "logged",
            "--plays",
            str(run / "logs" / "plays.jsonl"),
            "--schedule",
            str(run / SCHEDULE_FILE),
            "--out",
            str(tmp_path / "skew"),
        ]
    )
    assert code == 0
    summary = json.loads((tmp_path / "skew" / "logged-onsets-summary.json").read_text("utf-8"))
    assert summary["n_slots"] == 9 and summary["n_rows"] == 16 and summary["complete_rows"] == 16
    lines = (tmp_path / "skew" / "logged-onsets.csv").read_text("utf-8").splitlines()
    assert lines[0].endswith("onset_ms_S1,onset_ms_S2,onset_ms_S3") and len(lines) == 17
    with pytest.raises(SystemExit):
        panel_demo_main(["--speed", "2"])
    with pytest.raises(SystemExit):
        panel_demo_main(["--stations", "S1,S2", "--raters", "R01"])
