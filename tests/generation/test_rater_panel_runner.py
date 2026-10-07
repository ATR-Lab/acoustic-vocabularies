"""The rater panel (#21) in the study-mode batch runner (#20): keyed station sessions.

`batch_runner.serve_panel` draws a fresh access secret for every session, and the
operator gets one keyed URL per station. Here three bot stations (`rater.BotRater`) join
through those URLs, from the `on_panel` hook the dry run (#22) uses, and speak the real
rater protocol over HTTP and WebSockets while the orchestrator runs appointment 1 of the
DEMO batch (simulated proposers) in accelerated real time: 144 rating slots per rater,
the counts of one appointment. The other tests check that wrong and missing keys are
refused, that the secret never reaches a log or a run file, and that the command line
prints the keyed URLs and refuses a station session over several appointments.
"""

import base64
import collections
import json
import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from websockets.sync.client import connect

from av_generation import _batch_sim as sim
from av_generation import batch_runner as br
from av_generation.clock import ManualClock, ScaledClock
from av_generation.ids import RunKind
from av_generation.panel import create_panel_app, seat_key, station_url
from av_generation.rater import BotRater, BotRatingPolicy, bot_rating
from av_generation.rater_protocol import PROTOCOL_VERSION, STATION_PAGE, WS_PATH
from av_generation.records import PlayEvent, RatingRecord, TimingEvent, read_records

ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "DEMO-A-stations-01"
SPEED = 100
"""Clock speed of the station run: one 20-s slot is 200 ms of real time."""
SEATS = (("S1", "R01"), ("S2", "R02"), ("S3", "R03"))


def demo_inputs():
    return br.load_batch_inputs(
        config=ROOT / "generation/examples/demo-batch-config.json",
        meanings=ROOT / "generation/examples/demo-meanings",
        fallback=ROOT / "sound" / sim.DEMO_FALLBACK_MANIFEST,
        proposers="sim",
    )


def secret_forms(secret):
    """Every text and byte form in which a 32-byte secret could leak."""
    texts = {
        secret.hex(),
        secret.hex().upper(),
        base64.b64encode(secret).decode(),
        base64.urlsafe_b64encode(secret).decode(),
        base64.urlsafe_b64encode(secret).decode().rstrip("="),
        repr(secret),
    }
    return texts, {secret, *(t.encode() for t in texts)}


class _Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.texts = []

    def emit(self, record):
        self.texts.append(f"{record.name} {record.getMessage()} {record.exc_text or ''}")


# ---------------------------------------------------------------------------
# One appointment with three keyed bot stations


@pytest.fixture(scope="module")
def station_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("runs")
    if os.environ.get("CI") == "true":
        out = ROOT / "generation/out/ci/rater-panel-runner"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True, exist_ok=True)
    clock = ScaledClock(SPEED)
    batch = br.open_batch(demo_inputs(), out / RUN_ID, kind="demo", clock=clock, proposers="sim")
    secrets_drawn, hooked, results, threads = [], [], {}, []

    def spy_panel_app(host, *, clock, access_secret):
        secrets_drawn.append(access_secret)
        return create_panel_app(host, clock=clock, access_secret=access_secret)

    def start_bot_stations(base_url):
        """What the dry run (#22) does in `on_panel`: one bot per keyed station URL."""
        urls = br.station_urls(batch)
        hooked.append((base_url, urls))
        for url in urls.values():
            bot = BotRater.from_station_url(
                url, run_id=RUN_ID, policy=BotRatingPolicy(), clock=clock
            )
            thread = threading.Thread(
                target=lambda b=bot: results.__setitem__(b.station, b.run()), daemon=True
            )
            threads.append(thread)
            thread.start()

    records = _Records()
    root, ours = logging.getLogger(), logging.getLogger("av_generation")
    levels = root.level, ours.level
    root.addHandler(records)
    root.setLevel(logging.INFO)
    ours.setLevel(logging.DEBUG)
    lines = []
    started = time.monotonic()
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(br, "create_panel_app", spy_panel_app)
            nxt = br.run_session(
                batch,
                appointment=1,
                panel="stations",
                panel_port=0,
                on_panel=start_bot_stations,
                station_timeout_s=60,
                log=lines.append,
            )
        elapsed_s = time.monotonic() - started
        for thread in threads:
            thread.join(60)
    finally:
        root.removeHandler(records)
        root.setLevel(levels[0])
        ours.setLevel(levels[1])
    summary = sim.summarize(batch.layout, batch.orchestrator.config)
    lags = sorted(o - s for r in results.values() for (_, _, s, o) in r.plays)
    summary["stations"] = {
        station: {
            "end_reason": r.end_reason,
            "slots": len(r.slots),
            "plays": len(r.plays),
            "ratings_accepted": sum(1 for ok, _ in r.ratings.values() if ok),
            "ratings_refused": dict(
                collections.Counter(c for ok, c in r.ratings.values() if not ok)
            ),
            "missing": len(r.missing),
            "reconnects": r.reconnects,
        }
        for station, r in sorted(results.items())
    }
    summary["bot_onset_lag_clock_ms"] = {
        "median": lags[len(lags) // 2],
        "max": lags[-1],
        "speed": SPEED,
    }
    summary["elapsed_real_s"] = round(elapsed_s, 1)
    summary_path = out / f"{RUN_ID}-summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return SimpleNamespace(
        batch=batch,
        next_atom=nxt,
        lines=lines,
        hooked=hooked,
        secrets=secrets_drawn,
        results=results,
        log_texts=records.texts,
        summary=summary,
        files=[*batch.layout.root.rglob("*"), summary_path],
    )


def test_keyed_bot_stations_run_an_appointment_through_the_runner(station_run):
    run = station_run
    batch, config = run.batch, run.batch.orchestrator.config
    assert run.next_atom == config.atom_order[4]
    # the operator's lines: one keyed URL per station, then the session
    [secret] = run.secrets
    [(base, urls)] = run.hooked
    keyed = {
        station: station_url(base, station, rater, key=seat_key(secret, rater, station))
        for station, rater in SEATS
    }
    assert urls == keyed
    assert run.lines[:4] == [
        *(f"Rater station {s} ({r}): {keyed[s]}" for s, r in SEATS),
        "Waiting for stations: S1, S2, S3",
    ]
    assert run.lines[-1] == f"Atoms finished: 4/16; next: {run.next_atom}"
    # the keys end with the session, and so does the panel session of this batch
    assert batch.served_panel is None
    with pytest.raises(br.RunnerError) as err:
        br.station_urls(batch)
    assert err.value.code == "E_STATIONS"
    with pytest.raises(br.RunnerError) as err:  # the next appointment: reopen the run
        br.run_session(batch, panel="stations", panel_port=0, station_timeout_s=1)
    assert err.value.code == "E_MODE" and "reopen the run" in str(err.value)
    # the counts of one appointment
    counts = run.summary["counts"]
    assert counts["slot_records"] == 144 and counts["decision_records"] == 48
    assert counts["rating_records_per_rater"] == {"R01": 144, "R02": 144, "R03": 144}
    assert counts["commits_in_final_books"] == 12
    # every station was admitted with its key and followed every slot to the end
    assert sorted(run.results) == ["S1", "S2", "S3"]
    for result in run.results.values():
        assert result.end_reason == "appointment_complete"
        assert len(result.slots) == 144
        codes = {code for code, _ in result.errors}
        assert not codes & {"E_UNKNOWN_RATER", "E_REPLACED", "E_PROTOCOL", "E_UNKNOWN_SLOT"}
        assert result.assets and all(result.assets.values())


def test_ratings_and_plays_of_the_bot_stations_reach_the_run_logs(station_run):
    """Each stored rating is exactly what a station sent over the protocol and the host
    accepted; each rated slot has the station's `played` reports of its sounds."""
    layout = station_run.batch.layout
    ratings = read_records(layout.log("rating"), RatingRecord)
    plays = read_records(layout.log("play"), PlayEvent)
    heard = collections.defaultdict(set)
    for play in plays:
        heard[(play.station, play.rating_slot_id)].add(play.context)
    rateable = [r for r in ratings if not r.placeholder]
    rated = [r for r in rateable if not r.missing]
    assert len(rated) >= len(rateable) // 2  # slow runners may miss a few locks
    for rater_id, station in (("R01", "S1"), ("R02", "S2"), ("R03", "S3")):
        accepted = {sid for sid, (ok, _) in station_run.results[station].ratings.items() if ok}
        assert {r.rating_slot_id for r in rated if r.rater_id == rater_id} == accepted
    for record in rated:
        draw = bot_rating(
            RUN_ID,
            record.rater_id,
            record.rating_slot_id,
            ask_distinguishability=not record.first_atom,
            policy=BotRatingPolicy(),
        )
        assert (record.association, record.comfort) == (draw.association, draw.comfort)
        if record.first_atom:  # not asked: stored as 4 by rule
            assert record.distinguishability == 4 and record.distinguishability_by_rule
        else:
            assert record.distinguishability == draw.distinguishability
        assert record.rater_kind == "bot" and record.candidate_onset_ms is not None
        contexts = heard[(record.station, record.rating_slot_id)]
        assert "rating_candidate" in contexts
        if record.reference_onset_ms is not None:
            assert "rating_reference" in contexts
    assert {r.first_atom for r in rated} == {True, False}
    assert sum(1 for r in ratings if r.placeholder) % 3 == 0
    timing = read_records(layout.log("timing"), TimingEvent)
    connects = collections.Counter(e.station for e in timing if e.event == "station_connect")
    assert connects == {"S1": 1, "S2": 1, "S3": 1}
    assert {e.station for e in timing if e.event == "clock_sync"} == {"S1", "S2", "S3"}


def test_the_session_secret_never_reaches_logs_or_run_files(station_run):
    [secret] = station_run.secrets
    assert isinstance(secret, bytes) and len(secret) == 32
    texts, blobs = secret_forms(secret)
    keys = {seat_key(secret, rater, station) for station, rater in SEATS}
    files = [p for p in station_run.files if p.is_file()]
    assert len(files) > 10
    for path in files:
        data = path.read_bytes()
        assert not any(blob in data for blob in blobs), path.name
        assert not any(key.encode() in data for key in keys), path.name
    for text in [*station_run.lines, *station_run.log_texts]:
        assert not any(form in text for form in texts)
    # the seat keys appear only in the operator's station lines (and in the request log
    # of the bots' own HTTP client, which is the station's side, not the runner's)
    with_keys = [line for line in station_run.lines if any(k in line for k in keys)]
    assert with_keys == station_run.lines[:3]
    leaked = [
        text
        for text in station_run.log_texts
        if any(k in text for k in keys) and not text.startswith("httpx ")
    ]
    assert not leaked, leaked[:3]


# ---------------------------------------------------------------------------
# Wrong and missing keys


def _hello(station, rater):
    return json.dumps(
        {
            "type": "hello",
            "protocol_version": PROTOCOL_VERSION,
            "station": station,
            "rater_id": rater,
            "kind": "bot",
            "client_ms": 0.0,
            "resume_rating_slot_id": None,
        }
    )


def _join(base_url, station, rater, key):
    query = "" if key is None else f"?key={key}"
    with connect("ws" + base_url[len("http") :] + WS_PATH + query, open_timeout=10) as ws:
        ws.send(_hello(station, rater))
        return json.loads(ws.recv(timeout=10))


def test_a_station_with_a_wrong_or_missing_key_is_refused(tmp_path):
    batch = sim.make_sim_batch(tmp_path, "DEMO-keys-01", clock=ManualClock())
    orch = batch.orchestrator
    host = orch.panel_host()
    policy = BotRatingPolicy()
    with br.serve_panel(host, clock=batch.clock, port=0) as served:
        base, urls = served.base_url, served.station_urls
        key_s1 = urls["S1"].rsplit("key=", 1)[1]
        key_s2 = urls["S2"].rsplit("key=", 1)[1]
        assert len({key_s1, key_s2}) == 2 and len(key_s1) == 32
        wrong = {
            "no key": station_url(base, "S1", "R01"),
            "made-up key": station_url(base, "S1", "R01", key="0" * 32),
            "another seat's key": station_url(base, "S1", "R01", key=key_s2),
            "the key on another seat": station_url(base, "S2", "R02", key=key_s1),
        }
        for name, url in wrong.items():
            result = BotRater.from_station_url(
                url, run_id="DEMO-keys-01", policy=policy, max_reconnects=0
            ).run()
            assert result.errors and result.errors[0][0] == "E_UNKNOWN_RATER", name
            assert result.end_reason is None and result.slots == [], name
        assert _join(base, "S1", "R02", key_s1)["code"] == "E_UNKNOWN_RATER"  # rater swapped
        assert _join(base, "S1", "R01", None)["code"] == "E_UNKNOWN_RATER"
        assert all(not up for _, up in orch.console().stations)
        # assets: no key or a wrong key is 403; a seat's key passes (404: no such asset yet)
        asset = f"{base}/panel/assets/{'a' * 64}.wav"
        assert httpx.get(asset, trust_env=False).status_code == 403
        assert httpx.get(asset, params={"key": "0" * 32}, trust_env=False).status_code == 403
        assert httpx.get(asset, params={"key": key_s2}, trust_env=False).status_code == 404
        # the right key opens its seat
        welcome = _join(base, "S1", "R01", key_s1)
        assert welcome["type"] == "welcome" and welcome["station"] == "S1"
    timing = read_records(batch.layout.log("timing"), TimingEvent)
    assert [e.station for e in timing if e.event == "station_connect"] == ["S1"]


def test_station_urls_parse_into_bots_and_refuse_other_urls():
    url = station_url("http://127.0.0.1:8765/", "S2", "R02", key="ab" * 16)
    bot = BotRater.from_station_url(url, run_id="DEMO-keys-02", policy=BotRatingPolicy())
    assert (bot.base_url, bot.station, bot.rater_id) == ("http://127.0.0.1:8765", "S2", "R02")
    assert bot.ws_url == f"ws://127.0.0.1:8765{WS_PATH}?key={'ab' * 16}"
    keyless = BotRater.from_station_url(
        station_url("http://127.0.0.1:8765", "S1", "R01"),
        run_id="DEMO-keys-02",
        policy=BotRatingPolicy(),
    )
    assert keyless.ws_url == f"ws://127.0.0.1:8765{WS_PATH}"
    for bad in (
        "ftp://127.0.0.1:8765/panel/station?station=S1&rater=R01",
        "http://127.0.0.1:8765/elsewhere?station=S1&rater=R01",
        f"http://127.0.0.1:8765{STATION_PAGE}?station=S1",
        f"http://127.0.0.1:8765{STATION_PAGE}?station=S1&station=S2&rater=R01",
    ):
        with pytest.raises(ValueError) as err:
            BotRater.from_station_url(bad, run_id="DEMO-keys-02", policy=BotRatingPolicy())
        assert "key=" not in str(err.value)


# ---------------------------------------------------------------------------
# Command line


class _Operator:
    """Standard output of the command line, read by an operator who opens the printed
    URLs of stations S1 and S2 (bot stations) and never opens S3's."""

    def __init__(self, run_id):
        self.run_id, self.lines, self.bots, self._partial = run_id, [], [], ""

    def write(self, text):
        *done, self._partial = (self._partial + text).split("\n")
        for line in done:
            self.lines.append(line)
            if line.startswith(("Rater station S1 ", "Rater station S2 ")):
                bot = BotRater.from_station_url(
                    line.split(": ", 1)[1],
                    run_id=self.run_id,
                    policy=BotRatingPolicy(),
                    max_reconnects=0,
                )
                thread = threading.Thread(target=bot.run, daemon=True)
                self.bots.append(thread)
                thread.start()
        return len(text)

    def flush(self):
        pass

    def isatty(self):  # uvicorn's log formatter asks
        return False


def test_cli_prints_keyed_urls_that_open_the_seats(tmp_path, monkeypatch, capsys):
    """The operator opens the printed URLs: two stations join with them; the session
    waits for the third and stops with `E_STATIONS` naming only it."""
    operator = _Operator("DEMO-cli-stations-01")
    monkeypatch.setattr(sys, "stdout", operator)
    common = ["--kind", "demo", "--proposers", "sim", "--clock", "scaled", "--speed", "100"]
    run_dir = tmp_path / "DEMO-cli-stations-01"
    argv = ["run", "--run-dir", str(run_dir), *common, "--panel-port", "0"]
    assert br.main([*argv, "--station-timeout-s", "8"]) == 1
    assert "E_STATIONS: stations not connected: S3" in capsys.readouterr().err
    for thread in operator.bots:
        thread.join(30)
    keyed = [line for line in operator.lines if line.startswith("Rater station ")]
    assert [line.split(" (")[0] for line in keyed] == [
        "Rater station S1",
        "Rater station S2",
        "Rater station S3",
    ]
    assert all(f"{STATION_PAGE}?station=" in line and "&key=" in line for line in keyed)
    timing = read_records(run_dir / "logs/timing.jsonl", TimingEvent)
    assert sorted(e.station for e in timing if e.event == "station_connect") == ["S1", "S2"]
    # a station session is one appointment: refused before the run directory exists
    other = tmp_path / "DEMO-cli-stations-02"
    assert br.main(["run", "--run-dir", str(other), *common, "--appointment", "all"]) == 1
    assert "E_MODE: a station session runs one appointment" in capsys.readouterr().err
    assert not other.exists()


def test_run_session_refuses_all_appointments_with_stations(tmp_path):
    batch = br.open_batch(
        demo_inputs(),
        tmp_path / "DEMO-all-01",
        kind="demo",
        clock=ScaledClock(100),
        proposers="sim",
    )
    with pytest.raises(br.RunnerError) as err:
        br.run_session(batch, appointment="all", panel="stations", panel_port=0)
    assert err.value.code == "E_MODE"
    assert batch.kind is RunKind.DEMO and batch.served_panel is None
