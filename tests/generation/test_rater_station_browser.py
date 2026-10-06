"""Rater station page in Chromium (#21): no replay, control locking, first-atom rule,
placeholders, reconnects, pause and withdrawal. Real time: each slot takes 20 s.

The page keeps its Content-Security-Policy in these tests (no eval), so state is read by
polling `page.evaluate`. An init script counts every `AudioBufferSourceNode.start` and
media-element `play()` call and keeps the page's WebSocket objects (test-only
instrumentation; the page itself has no test hooks that play audio).
"""

import contextlib
import hashlib
import json
import random
import time

import pytest

from av_generation.clock import SystemClock
from av_generation.config import RaterSeat
from av_generation.constants import RATING_SLOT_MS
from av_generation.masking import masking_findings
from av_generation.panel import ONSET_TOLERANCE_MS, create_panel_app, station_url
from av_generation.panel_demo import ScriptedPanelHost, demo_session
from av_generation.rater_protocol import STATION_PAGE

pytestmark = pytest.mark.browser

NO_PLAYER = "audio, video, [controls], input[type=range]"
HUMANS = tuple(RaterSeat(f"R0{i}", f"S{i}", "human") for i in (1, 2, 3))

INIT = r"""
(() => {
  window.__avStarts = [];
  const start = AudioBufferSourceNode.prototype.start;
  AudioBufferSourceNode.prototype.start = function (...args) {
    window.__avStarts.push({ t: performance.now(), samples: this.buffer ? this.buffer.length : 0 });
    return start.apply(this, args);
  };
  window.__avMediaPlays = 0;
  const play = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function (...args) {
    window.__avMediaPlays += 1;
    return play.apply(this, args);
  };
  window.__avSockets = [];
  const Native = window.WebSocket;
  window.WebSocket = class extends Native {
    constructor(...args) {
      super(...args);
      window.__avSockets.push(this);
    }
  };
})();
"""


def wait_until(predicate, timeout=15.0, step=0.05):
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        time.sleep(step)


def sleep_until(host, ms):
    """Sleep until the host's run clock reaches `ms`."""
    while host.clock.now_ms() < ms:
        time.sleep(min(0.05, max(0.001, (ms - host.clock.now_ms()) / 1000)))


def state(page):
    return page.evaluate("() => window.avStation.state()")


def audio_starts(page):
    """Real audio starts (the 1-sample unlock buffer at Start is not a sound)."""
    return [s for s in page.evaluate("() => window.__avStarts") if s["samples"] > 1]


class Panel:
    def __init__(self, chromium, serve_app, session):
        self.clock = SystemClock()
        self.host = ScriptedPanelHost(session, clock=self.clock)
        self.base = serve_app(create_panel_app(self.host, clock=self.clock))
        self.browser = chromium
        self.contexts = []
        self.frames = {}

    def open(self, seat, *, start=True):
        context = self.browser.new_context()
        self.contexts.append(context)
        page = context.new_page()
        page.add_init_script(INIT)
        frames = self.frames.setdefault(seat.station, [])
        page.on(
            "websocket",
            lambda ws: ws.on("framereceived", lambda payload: frames.append(json.loads(payload))),
        )
        page.goto(station_url(self.base, seat.station, seat.rater_id))
        if start:
            page.click("#start-button")
            wait_until(lambda: state(page)["offset"] is not None)
        return page

    def slot(self, position):
        return next(s for s in self.host.absolute_slots() if s.position == position)

    def close(self):
        self.host.stop()
        for context in self.contexts:
            with contextlib.suppress(Exception):  # some tests close their context
                context.close()


@pytest.fixture
def panel(chromium, serve_app):
    made = []

    def make(session):
        made.append(Panel(chromium, serve_app, session))
        return made[-1]

    yield make
    for p in made:
        p.close()


def controls_disabled(page):
    return page.evaluate(
        "() => Array.from(document.querySelectorAll('#rating-form .scale button'))"
        ".every((b) => b.disabled)"
    )


def rate(page, association, distinguishability, comfort):
    page.click(f"#q-association button[data-value='{association}']")
    if distinguishability is not None:
        page.click(f"#q-distinguishability button[data-value='{distinguishability}']")
    page.click(f"#q-comfort button[data-value='{comfort}']")
    page.click("#submit-rating")


def test_station_sha256_matches_hashlib(panel):
    p = panel(demo_session(seats=HUMANS, rounds=1, positions=[1]))
    page = p.open(HUMANS[0], start=False)
    rng = random.Random(21)
    for size in (0, 1, 55, 56, 63, 64, 65, 1000, 19_244):
        data = bytes(rng.randrange(256) for _ in range(size))
        expected = hashlib.sha256(data).hexdigest()
        js = page.evaluate("(a) => window.avStation.sha256HexJs(new Uint8Array(a))", list(data))
        assert js == expected, size
        native = page.evaluate(
            "async (a) => window.avStation.sha256Hex(new Uint8Array(a).buffer)", list(data)
        )
        assert native == expected, size


def test_one_round_on_three_stations_plays_once_and_locks_controls(panel):
    session = demo_session(
        seats=HUMANS, rounds=1, positions=[1, 5], placeholders=[(1, 5)], lead_in_ms=4_000
    )
    p = panel(session)
    pages = [p.open(seat) for seat in HUMANS]
    p.host.begin()
    p.host.start()
    s1 = p.slot(1)
    unlock = s1.start_ms + s1.unlock_offset_ms
    # Before the unlock: candidate playing, controls locked, reference meaning not yet shown.
    sleep_until(p.host, s1.start_ms + 1_000)
    for page in pages:
        assert page.is_visible("#screen-slot")
        assert page.text_content("#meaning") == s1.meaning
        assert controls_disabled(page) and page.is_disabled("#submit-rating")
        assert page.text_content("#reference-meaning") != s1.reference_meaning
        # no replay, seek or volume control anywhere on the page
        assert page.evaluate("(q) => document.querySelectorAll(q).length", NO_PLAYER) == 0
    texts = pages[0].evaluate(
        "() => Array.from(document.querySelectorAll('#screen-slot button'), (b) => b.textContent)"
    )
    assert sorted(texts) == sorted(
        [str(i) for i in range(1, 8)] * 2 + ["Acceptable", "Unacceptable", "Submit"]
    )
    # Trying to replay: keys, clicks and a re-sent slot message play nothing again.
    pages[0].keyboard.press("Space")
    pages[0].keyboard.press("Enter")
    pages[0].keyboard.press("r")
    pages[0].click("#meaning")
    p.host.republish(s1.rating_slot_id)
    sleep_until(p.host, s1.start_ms + 2_300)
    assert all(page.text_content("#reference-meaning") == s1.reference_meaning for page in pages)
    sleep_until(p.host, unlock + 300)
    assert not any(controls_disabled(page) for page in pages)
    rate(pages[0], 6, 2, "acceptable")
    rate(pages[1], 3, 5, "unacceptable")
    wait_until(lambda: pages[0].text_content("#slot-status") == "Saved. Thank you.")
    assert controls_disabled(pages[0]) and page_after_submit_locked(pages[0])
    pages[0].keyboard.press("Space")
    # At 20 s the controls lock and the station waits; S3 never rated.
    sleep_until(p.host, s1.start_ms + RATING_SLOT_MS + 400)
    assert pages[2].is_visible("#screen-placeholder") or pages[2].is_visible("#screen-wait")
    assert controls_disabled(pages[2])
    # The placeholder slot: neutral screen, no controls, no audio.
    s5 = p.slot(5)
    sleep_until(p.host, s5.start_ms + 2_500)
    for page in pages:
        assert page.is_visible("#screen-placeholder") and not page.is_visible("#rating-form")
    assert p.host.wait_ended(40)
    # Each station started exactly two sounds (candidate, reference) at the scheduled
    # times: nothing was replayed, the placeholder played nothing.
    for page in pages:
        assert len(audio_starts(page)) == 2
        assert page.evaluate("() => window.__avMediaPlays") == 0
        assert sorted(state(page)["played"]) == [
            f"{s1.rating_slot_id}|candidate",
            f"{s1.rating_slot_id}|reference",
        ]
    plays = p.host.plays
    assert len(plays) == 6 and len({(x.station, x.context) for x in plays}) == 6
    for x in plays:
        assert abs(x.onset_ms - x.scheduled_ms) <= ONSET_TOLERANCE_MS, x
    for context in ("rating_candidate", "rating_reference"):
        onsets = [x.onset_ms for x in plays if x.context == context]
        assert max(onsets) - min(onsets) <= 100
    records = {(r.station, r.position): r for r in p.host.ratings}
    r1, r2, r3 = (records[(s, 1)] for s in ("S1", "S2", "S3"))
    assert (r1.association, r1.distinguishability, r1.comfort) == (6, 2, "acceptable")
    assert (r2.association, r2.distinguishability, r2.comfort) == (3, 5, "unacceptable")
    assert r3.missing and r3.association is None
    assert r1.rt_ms is not None and r1.rt_ms >= 300 and r1.rater_kind == "human"
    assert all(records[(s, 5)].placeholder for s in ("S1", "S2", "S3"))
    # Privacy: a station only ever receives the answer to its own rating.
    acks = {s: [f for f in frames if f["type"] == "rating_ack"] for s, frames in p.frames.items()}
    assert [a["accepted"] for a in acks["S1"]] == [True]
    assert [a["accepted"] for a in acks["S2"]] == [True]
    assert acks["S3"] == []
    for frames in p.frames.values():
        for frame in frames:
            assert masking_findings(json.dumps(frame)) == ()
            assert "DEMO-BK" not in json.dumps(frame)


def page_after_submit_locked(page):
    return page.is_disabled("#submit-rating")


def test_first_atom_slot_hides_distinguishability_and_stores_four(panel):
    session = demo_session(seats=HUMANS, rounds=1, positions=[1], atom_index=0, lead_in_ms=3_000)
    p = panel(session)
    page = p.open(HUMANS[0])
    p.host.begin()
    p.host.start()
    s1 = p.slot(1)
    sleep_until(p.host, s1.start_ms + 2_300)
    assert page.is_visible("#q-association") and page.is_visible("#q-comfort")
    assert not page.is_visible("#q-distinguishability")
    assert page.text_content("#reference-meaning") == "No comparison sound in this slot."
    sleep_until(p.host, s1.start_ms + s1.unlock_offset_ms + 200)
    rate(page, 4, None, "acceptable")
    wait_until(lambda: page.text_content("#slot-status") == "Saved. Thank you.")
    assert p.host.wait_ended(30)
    record = next(r for r in p.host.ratings if r.station == "S1")
    assert (record.distinguishability, record.distinguishability_by_rule) == (4, True)
    assert len(audio_starts(page)) == 1  # candidate only: the silence is kept


def test_reconnect_mid_slot_never_replays(panel):
    session = demo_session(seats=HUMANS, rounds=1, positions=[1, 2, 3], lead_in_ms=3_000)
    p = panel(session)
    page = p.open(HUMANS[0])
    p.host.begin()
    p.host.start()
    s1, s2, s3 = (p.slot(i) for i in (1, 2, 3))
    # Slot 1: the socket drops after the candidate onset and comes back. The page keeps
    # its state: no replay; the reference plays once; the rater can still rate.
    sleep_until(p.host, s1.start_ms + 500)
    page.evaluate("() => window.__avSockets.at(-1).close()")
    wait_until(lambda: page.evaluate("() => window.__avSockets.length") == 2)
    wait_until(lambda: state(page)["connected"])
    sleep_until(p.host, s1.start_ms + s1.unlock_offset_ms + 200)
    rate(page, 5, 5, "acceptable")
    wait_until(lambda: page.text_content("#slot-status") == "Saved. Thank you.")
    assert len(audio_starts(page)) == 2
    # Slot 2: the page reloads mid-slot (same tab): the slot is never handled again.
    sleep_until(p.host, s2.start_ms + 500)
    page.reload()
    page.click("#start-button")
    wait_until(lambda: state(page)["connected"])
    sleep_until(p.host, s2.start_ms + 3_000)
    assert audio_starts(page) == [] and not page.is_visible("#rating-form")
    # Slot 3: the station restarts with a fresh browser (no stored state) mid-slot: a
    # neutral rejoin screen, no audio, no controls.
    sleep_until(p.host, s3.start_ms + 600)
    page.context.close()
    fresh = p.open(HUMANS[0])
    sleep_until(p.host, s3.start_ms + 3_000)
    assert fresh.is_visible("#screen-rejoin") and audio_starts(fresh) == []
    assert state(fresh)["mode"] == "rejoin"
    assert p.host.wait_ended(40)
    records = {r.position: r for r in p.host.ratings if r.station == "S1"}
    assert records[1].reconnected and not records[1].missing and records[1].association == 5
    assert records[2].reconnected and records[2].missing
    assert records[3].reconnected and records[3].missing
    plays = [(x.rating_slot_id, x.context) for x in p.host.plays if x.station == "S1"]
    assert len(plays) == len(set(plays))  # no asset reported twice
    assert (s1.rating_slot_id, "rating_reference") in plays
    assert (s2.rating_slot_id, "rating_reference") not in plays
    # the old page played slot 3's candidate before it closed; the fresh page played nothing
    assert [c for slot_id, c in plays if slot_id == s3.rating_slot_id] == ["rating_candidate"]


def test_setup_pause_withdrawal_and_unknown_station(panel):
    p = panel(demo_session(seats=HUMANS, rounds=1, positions=[1]))
    context = p.browser.new_context()
    p.contexts.append(context)
    page = context.new_page()
    page.add_init_script(INIT)
    page.goto(p.base + STATION_PAGE)
    assert page.is_visible("#setup")
    page.click("#start-button")
    assert "Enter the station" in page.text_content("#start-message")
    page.fill("#setup-station", "S2")
    page.fill("#setup-rater", "R02")
    page.click("#start-button")
    wait_until(lambda: state(page)["connected"])
    assert page.text_content("#station-label") == "Station S2"
    p.host.operator("pause", "operator")
    wait_until(lambda: page.is_visible("#screen-pause"))
    p.host.operator("resume")
    wait_until(lambda: page.is_visible("#screen-wait"))
    page.click("#withdraw-button")
    assert page.is_visible("#withdraw-dialog")
    page.click("#withdraw-cancel")
    assert not page.is_visible("#withdraw-dialog")
    page.click("#withdraw-button")
    page.click("#withdraw-dialog [data-reason='discomfort']")
    wait_until(lambda: p.host.withdrawals == [("R02", "S2", "discomfort")])
    assert page.is_visible("#screen-withdrawn") and not page.is_visible("#withdraw-button")
    time.sleep(1.5)
    assert page.evaluate("() => window.__avSockets.length") == 1  # no reconnect
    stranger = p.open(RaterSeat("R09", "S9", "human"), start=False)
    stranger.click("#start-button")
    wait_until(lambda: stranger.is_visible("#screen-error"))
    assert "not part of the session" in stranger.text_content("#error-text")
