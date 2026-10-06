"""Listening sessions of the threshold tool (#23): run directory, runner, web app, bot
listener, play logging (each pair played exactly once), export and command line."""

import dataclasses
import json
import os
import shutil
from pathlib import Path

import httpx
import pytest

from av_generation import threshold as th
from av_generation import threshold_runner as tr
from av_generation.clock import ManualClock, SystemClock
from av_generation.ids import RunKind
from av_generation.records import (
    PlayEvent,
    RunManifest,
    ThresholdConfig,
    ThresholdSession,
    ThresholdTrial,
    TimingEvent,
    read_records,
)
from av_generation.rundir import RunPolicyError, run_layout
from av_generation.threshold_cli import main, run_demo

ROOT = Path(__file__).resolve().parents[2]
DEMO_SET = ROOT / "generation" / "examples" / "threshold" / "demo-stimuli.json"
CI_OUT = ROOT / "generation" / "out" / "ci" / "threshold"

TINY = ThresholdConfig(
    profiles=("P2",),
    bin_centers=("0.075", "0.175"),
    bin_halfwidth="0.0125",
    pairs_per_bin=2,
    same_pairs=2,
    gap_ms=500,
    threshold_default="0.10",
)
"""6 trials: P2 x 2 bins x 2 pairs + 2 catch pairs."""

FORBIDDEN_KEYS = {"pair_id", "kind", "bin_center", "distance", "profile", "correct", "order"}


@pytest.fixture(scope="module")
def tiny_set():
    return th.generate_stimuli("DEMO-T-tiny", TINY)


def _session(stimuli, session_id="DEMO-S01", *, tryout=False, listener="L01"):
    return th.plan_session(
        stimuli,
        session_id,
        listener_id=listener,
        station="S2",
        gain_db=-12.0,
        tryout=tryout,
        created_utc="2026-11-23T09:00:00.000Z",
    )


def _open(tmp_path, stimuli, run_id="DEMO-th-run", kind=RunKind.DEMO, clock=None):
    return tr.open_threshold_run(tmp_path, run_id, kind, stimuli, clock=clock or ManualClock())


def _runner(layout, stimuli, session, clock=None):
    return tr.ThresholdRunner(layout, stimuli, session, clock=clock or ManualClock(), fsync=False)


def _client(serve_app, runner):
    return httpx.Client(base_url=serve_app(tr.create_threshold_app(runner)), timeout=30)


def _logs(layout):
    trials, plays = tr.read_run_records(layout)
    timing = read_records(layout.log("timing"), TimingEvent)
    return trials, plays, timing


def _no_pair_information(payload):
    assert not FORBIDDEN_KEYS & set(payload), payload
    for value in payload.values():
        if isinstance(value, str):
            assert "same" not in value.lower() and "-0." not in value and "P2" not in value


# ---------------------------------------------------------------------------
# Run directory


def test_open_run_writes_manifest_and_set(tmp_path, tiny_set):
    layout = _open(tmp_path, tiny_set)
    manifest = RunManifest.read(layout.manifest)
    assert (manifest.purpose, manifest.kind, manifest.clock) == ("threshold", "demo", "manual")
    assert manifest.threshold == "0.10" and manifest.seed_namespace == "DEMO-T-tiny"
    assert tr.load_run(layout) == (tiny_set, [])
    assert _open(tmp_path, tiny_set).root == layout.root  # reopen: same set
    other = th.generate_stimuli("DEMO-T-tiny-2", TINY)
    with pytest.raises(tr.RunnerError, match="another stimulus set"):
        _open(tmp_path, other)
    with pytest.raises(tr.RunnerError, match="DEMO set in a pilot run"):
        _open(tmp_path, tiny_set, "TH-2026-01", RunKind.PILOT)
    assert tr.read_run_records(layout) == ([], [])


def test_real_runs_follow_the_restricted_policy(tmp_path):
    real = th.generate_stimuli("TH-2026-01", dataclasses.replace(TINY, same_pairs=0))
    with pytest.raises(tr.RunnerError, match="DEMO"):
        _open(tmp_path, th.generate_stimuli("DEMO-x1", TINY), "TH-run-1", RunKind.PILOT)
    with pytest.raises(tr.RunnerError, match="real"):
        _open(tmp_path, real, "DEMO-run-1", RunKind.DEMO)
    with pytest.raises(RunPolicyError) as err:
        _open(ROOT / "generation" / "out", real, "TH-run-1", RunKind.PILOT)
    assert err.value.code == "E_POLICY"
    layout = _open(tmp_path, real, "TH-run-1", RunKind.PILOT, clock=SystemClock())
    assert RunManifest.read(layout.manifest).clock == "real"
    with pytest.raises(tr.RunnerError, match="pilot threshold run"):
        _open(tmp_path, real, "TH-run-1", RunKind.CONFIRMATORY)
    broken = dataclasses.replace(real, pairs=real.pairs[1:])
    with pytest.raises(tr.RunnerError, match="problems"):
        _open(tmp_path, broken, "TH-run-2", RunKind.PILOT)


# ---------------------------------------------------------------------------
# Runner: a full session, play-once logging, no replay, no feedback


def test_full_session_plays_each_pair_exactly_once(tmp_path, tiny_set, serve_app):
    layout = _open(tmp_path, tiny_set)
    session = _session(tiny_set)
    runner = _runner(layout, tiny_set, session)
    # the session document exists before the first trial
    assert ThresholdSession.read(layout.threshold_session("DEMO-S01")) == session
    with _client(serve_app, runner) as client:
        assert tr.run_bot_session(client, session, tiny_set) == 6
        assert client.get("/threshold/api/state").json()["status"] == "done"
        assert client.post("/threshold/api/next").json()["error"] == "E_DONE"
    assert runner.complete
    trials, plays, timing = _logs(layout)
    assert len(trials) == 6 and len(plays) == 12
    assert all(p.result == "played" and p.audio_kind == "atom" for p in plays)
    check = th.check_plays(session, tiny_set, plays, trials)
    assert check.ok, check.problems
    assert (check.n_planned, check.n_played, check.n_answered, check.n_refused) == (6, 6, 6, 0)
    played = {}
    for event in plays:
        played.setdefault(event.trial_id, []).append(event.context)
    assert all(v == ["threshold_first", "threshold_second"] for v in played.values())
    by_trial = {t.trial_index: t for t in trials}
    for planned in session.plan:
        record = by_trial[planned.trial_index]
        assert (record.pair_id, record.order) == (planned.pair_id, planned.order)
        assert record.onset_first_ms <= record.onset_second_ms
        assert record.response in ("same", "different") and record.rt_ms is not None
        assert record.gap_ms == 500 and record.tryout is False
    events = [e.event for e in timing]
    assert events[0] == "session_start" and events[-1] == "session_end"
    assert {e.component for e in timing} == {"threshold"}


def test_no_replay_no_feedback_and_masked_api(tmp_path, tiny_set, serve_app):
    layout = _open(tmp_path, tiny_set)
    session = _session(tiny_set)
    runner = _runner(layout, tiny_set, session)
    client = _client(serve_app, runner)
    _check_masked_session(client, layout, session, tiny_set)
    client.close()


def _check_masked_session(client, layout, session, tiny_set):
    state = client.get("/threshold/api/state").json()
    assert state == {
        "n_trials": 6,
        "n_done": 0,
        "status": "running",
        "trial_index": 1,
        "phase": "listen",
    }
    trial = client.post("/threshold/api/next").json()
    _no_pair_information(trial)
    assert trial["phase"] == "listen" and trial["gap_ms"] == 500
    assert client.post("/threshold/api/next").json() == trial  # same tokens until played
    response = client.post("/threshold/api/trials/1/response", json={"response": "same"})
    assert response.status_code == 409 and response.json()["error"] == "E_STATE"
    not_fetched = client.post("/threshold/api/trials/1/played", json=_onsets())
    assert not_fetched.json()["error"] == "E_NOT_FETCHED"
    first = client.get(trial["first"])
    assert first.status_code == 200 and first.headers["content-type"] == "audio/wav"
    assert first.content[:4] == b"RIFF" and first.headers["cache-control"] == "no-store"
    again = client.get(trial["first"])
    assert again.status_code == 410 and again.json()["error"] == "E_TOKEN_USED"
    assert client.get(trial["second"]).status_code == 200
    assert client.get("/threshold/api/audio/" + "0" * 32).status_code == 404
    bad = client.post(
        "/threshold/api/trials/1/played", json={"onset_first_ms": 9, "onset_second_ms": 1}
    )
    assert bad.status_code == 422
    extra = client.post("/threshold/api/trials/1/played", json={**_onsets(), "x": 1})
    assert extra.status_code == 422
    assert (
        client.post("/threshold/api/trials/2/played", json=_onsets()).json()["error"] == "E_STATE"
    )
    assert client.post("/threshold/api/trials/1/played", json=_onsets()).json() == {"ok": True}
    replay = client.post("/threshold/api/trials/1/played", json=_onsets())
    assert replay.json()["error"] == "E_ALREADY_PLAYED"
    assert client.post("/threshold/api/next").json() == {
        "trial_index": 1,
        "n_trials": 6,
        "phase": "respond",
    }
    answered = client.post(
        "/threshold/api/trials/1/response", json={"response": "different", "rt_ms": 640}
    )
    body = answered.json()
    assert body == {"ok": True, "n_trials": 6, "n_done": 1, "status": "running"}  # no score
    _no_pair_information(body)
    trials, plays, _ = _logs(layout)
    refused = [p for p in plays if p.result == "refused"]
    assert [p.reason for p in refused] == ["E_TOKEN_USED", "E_ALREADY_PLAYED"]
    assert all(p.onset_ms is None for p in refused)
    check = th.check_plays(session, tiny_set, plays, trials, complete=False)
    assert check.ok and check.n_refused == 2 and check.n_played == 1
    assert not th.check_plays(session, tiny_set, plays, trials).ok  # session not complete
    page = client.get("/threshold/")
    assert page.status_code == 200 and "Same or different?" in page.text
    for name in ("threshold.js", "threshold.css"):
        assert client.get(f"/threshold/static/{name}").status_code == 200
    assert client.get("/threshold/static/index.html").status_code == 404
    assert client.get("/", follow_redirects=False).headers["location"] == "/threshold/"
    script = client.get("/threshold/static/threshold.js").text
    page_files = page.text + script + client.get("/threshold/static/threshold.css").text
    assert "http://" not in page_files and "https://" not in page_files  # no CDN, no links
    assert "<audio" not in page.text and "replay" not in page.text.lower()  # no replay control


def _onsets(first=100, second=1500):
    return {"onset_first_ms": first, "onset_second_ms": second}


def _play(client, index):
    trial = client.post("/threshold/api/next").json()
    assert trial["trial_index"] == index
    client.get(trial["first"]).raise_for_status()
    client.get(trial["second"]).raise_for_status()
    client.post(f"/threshold/api/trials/{index}/played", json=_onsets()).raise_for_status()


def test_resume_after_restart_never_replays(tmp_path, tiny_set, serve_app):
    layout = _open(tmp_path, tiny_set)
    session = _session(tiny_set)
    clock = ManualClock()
    first_runner = _runner(layout, tiny_set, session, clock)
    with _client(serve_app, first_runner) as client:
        _play(client, 1)
        client.post(
            "/threshold/api/trials/1/response", json={"response": "same"}
        ).raise_for_status()
        _play(client, 2)  # played, then the station crashes before the answer
        old_tokens = client.post("/threshold/api/next").json()
    # a torn line from the crash is repaired and logged
    with open(layout.log("play"), "ab") as handle:
        handle.write(b'{"record":"pl')
    second_runner = _runner(layout, tiny_set, session, clock)
    with _client(serve_app, second_runner) as client:
        state = client.get("/threshold/api/state").json()
        assert (state["trial_index"], state["phase"], state["n_done"]) == (2, "respond", 1)
        assert client.post("/threshold/api/next").json()["phase"] == "respond"
        assert old_tokens["phase"] == "respond"
        client.post(
            "/threshold/api/trials/2/response", json={"response": "different"}
        ).raise_for_status()
        trial = client.post("/threshold/api/next").json()
        assert trial["trial_index"] == 3 and trial["phase"] == "listen"
        _, plays, timing = _logs(layout)
        token = trial["first"].rsplit("/", 1)[1]
        assert token == second_runner._token(3, "first") != first_runner._token(3, "first")
    trials, plays, timing = _logs(layout)
    assert [t.trial_index for t in trials] == [1, 2]
    assert trials[1].onset_first_ms is not None  # onsets restored from the play log
    assert [e.event for e in timing].count("session_start") == 2
    assert any(e.event == "log_repaired" and "plays.jsonl" in e.detail for e in timing)
    assert th.check_plays(session, tiny_set, plays, trials, complete=False).ok
    # an existing session document with another plan is refused
    changed = dataclasses.replace(session, plan=tuple(reversed(session.plan)))
    with pytest.raises(tr.RunnerError):
        _runner(layout, tiny_set, changed)


def test_skip_records_a_missing_answer(tmp_path, tiny_set, serve_app):
    layout = _open(tmp_path, tiny_set)
    session = _session(tiny_set)
    runner = _runner(layout, tiny_set, session)
    with _client(serve_app, runner) as client:
        trial = client.post("/threshold/api/next").json()
        client.get(trial["first"]).raise_for_status()
        # audio failed before the second motif: the operator skips; it is never replayed
        assert client.post("/threshold/api/trials/1/skip").json()["n_done"] == 1
        assert client.get(trial["second"]).status_code == 410
        _play(client, 2)
        assert client.post("/threshold/api/trials/2/skip").json()["n_done"] == 2
        tr.run_bot_session(client, session, tiny_set)
    trials, plays, timing = _logs(layout)
    assert [t.response for t in trials[:2]] == [None, None]
    assert trials[0].onset_first_ms is None and trials[1].onset_first_ms is not None
    check = th.check_plays(session, tiny_set, plays, trials)
    assert check.ok, check.problems
    assert check.n_answered == 4 and check.n_played == 5
    assert sum(1 for e in timing if e.event == "operator_action") == 2
    rows = th.summarize(trials)
    assert sum(r["n_no_response"] for r in rows if r["profile"] == "all") == 2


def test_runner_refuses_wrong_assets_and_plans(tmp_path, tiny_set):
    pair = tiny_set.pairs[0]
    bad = dataclasses.replace(
        tiny_set, pairs=(dataclasses.replace(pair, file_sha256_a="0" * 64), *tiny_set.pairs[1:])
    )
    layout = _open(tmp_path, bad)
    with pytest.raises(tr.RunnerError) as err:
        _runner(layout, bad, _session(bad))
    assert err.value.code == tr.E_ASSET_HASH
    with pytest.raises(tr.RunnerError, match="another stimulus set"):
        _runner(layout, tiny_set, _session(tiny_set))
    good = _open(tmp_path, tiny_set, "DEMO-th-run-2")
    wrong = dataclasses.replace(_session(tiny_set), order_seed=1)
    with pytest.raises(tr.RunnerError, match="plan"):
        _runner(good, tiny_set, wrong)


def test_check_plays_detects_replays_and_wrong_assets(tmp_path, tiny_set, serve_app):
    layout = _open(tmp_path, tiny_set)
    session = _session(tiny_set)
    runner = _runner(layout, tiny_set, session)
    with _client(serve_app, runner) as client:
        tr.run_bot_session(client, session, tiny_set)
    trials, plays, _ = _logs(layout)
    assert th.check_plays(session, tiny_set, plays, trials).ok
    dup = [*plays, plays[0]]
    assert any(
        "more than once" in p for p in th.check_plays(session, tiny_set, dup, trials).problems
    )
    swapped = [dataclasses.replace(plays[0], asset_id=plays[1].asset_id), *plays[1:]]
    problems = th.check_plays(session, tiny_set, swapped, trials).problems
    assert any("not the planned motif" in p for p in problems)
    late = [dataclasses.replace(plays[0], onset_ms=10**9), *plays[1:]]
    assert any(
        "before the first" in p for p in th.check_plays(session, tiny_set, late, trials).problems
    )
    missing = [p for p in plays if p.trial_id != "DEMO-S01.t003"]
    assert any(
        "answered without" in p for p in th.check_plays(session, tiny_set, missing, trials).problems
    )
    stray = [*plays, dataclasses.replace(plays[0], trial_id="DEMO-S01.t099")]
    assert any("unplanned" in p for p in th.check_plays(session, tiny_set, stray, trials).problems)
    garbled = [*plays, dataclasses.replace(plays[0], trial_id="DEMO-S01.tx")]
    assert any(
        "malformed" in p for p in th.check_plays(session, tiny_set, garbled, trials).problems
    )
    twice = [*trials, trials[0]]
    assert any("twice" in p for p in th.check_plays(session, tiny_set, plays, twice).problems)
    wrong = [
        dataclasses.replace(trials[0], order="BA" if trials[0].order == "AB" else "AB"),
        *trials[1:],
    ]
    assert any(
        "match the plan" in p for p in th.check_plays(session, tiny_set, plays, wrong).problems
    )
    extra = [*trials, dataclasses.replace(trials[0], trial_index=99)]
    assert any(
        "unplanned trial 99" in p for p in th.check_plays(session, tiny_set, plays, extra).problems
    )
    other = [dataclasses.replace(p, trial_id="DEMO-S02.t001") for p in plays]
    assert th.check_plays(session, tiny_set, other, []).problems  # other sessions ignored


# ---------------------------------------------------------------------------
# Export, tryout separation, command line, demo artifact


def test_export_keeps_tryout_and_listener_sessions_apart(tmp_path, tiny_set, serve_app):
    layout = _open(tmp_path, tiny_set)
    for session_id, tryout, listener in (
        ("DEMO-S01", False, "L01"),
        ("DEMO-S02", False, "L02"),
        ("DEMO-T01", True, "TM1"),
    ):
        session = _session(tiny_set, session_id, tryout=tryout, listener=listener)
        runner = _runner(layout, tiny_set, session)
        with _client(serve_app, runner) as client:
            tr.run_bot_session(client, session, tiny_set)
    listeners = tr.export_run(layout, tmp_path / "listeners", tryout=False)
    tryout = tr.export_run(layout, tmp_path / "tryout", tryout=True, label="DEMO tryout label")
    assert [c.session_id for c in listeners.play_checks] == ["DEMO-S01", "DEMO-S02"]
    assert all(c.ok for c in listeners.play_checks + tryout.play_checks)
    assert listeners.summary["listeners"] == ["L01", "L02"] and listeners.summary["n_trials"] == 12
    assert tryout.summary["listeners"] == ["TM1"] and tryout.summary["tryout"] is True
    assert tryout.summary["label"] == "DEMO tryout label"
    assert listeners.summary["label"] == tr.SYNTHETIC_LABEL
    assert set(listeners.files) == {"trials.csv", "summary.csv", "fits.csv", "summary.json"}
    assert (tmp_path / "listeners" / "summary.png").exists()
    rows = th.read_trials_csv(tmp_path / "listeners" / "trials.csv")
    assert {t.session_id for t in rows} == {"DEMO-S01", "DEMO-S02"}
    summary = json.loads((tmp_path / "listeners" / "summary.json").read_text(encoding="utf-8"))
    assert summary["trials_csv_sha256"] == listeners.files["trials.csv"]
    assert summary["sessions"][0]["plays_ok"] is True and summary["sessions"][0]["gain_db"] == -12.0
    assert (
        tr.default_label(
            th.generate_stimuli("TH-1", dataclasses.replace(TINY, same_pairs=0)), tryout=True
        )
        == tr.TRYOUT_LABEL
    )


def test_command_line_session_export_and_checks(tmp_path, capsys, serve_app):
    stimuli_path = tmp_path / "stimuli.json"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(dataclasses.asdict(TINY)), encoding="utf-8")
    assert (
        main(
            [
                "stimuli",
                "--set-id",
                "DEMO-T-cli",
                "--config",
                str(config_path),
                "--out",
                str(stimuli_path),
            ]
        )
        == 0
    )
    built = json.loads(capsys.readouterr().out)
    assert built["n_pairs"] == 6 and built["coverage"]["P2|same"] == 2
    assert main(["check", "--stimuli", str(stimuli_path)]) == 0
    capsys.readouterr()
    base = [
        "session",
        "--runs-root",
        str(tmp_path / "runs"),
        "--run-id",
        "DEMO-cli-run",
        "--kind",
        "demo",
        "--stimuli",
        str(stimuli_path),
        "--session-id",
        "DEMO-S01",
        "--listener",
        "L01",
        "--station",
        "S3",
        "--gain-db",
        "-10.5",
        "--plan-only",
    ]
    assert main(base) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["n_trials"] == 6 and out["url"].endswith("/threshold/")
    session = ThresholdSession.read(out["session"])
    assert (session.station, session.gain_db) == ("S3", -10.5)
    assert main(base) == 0  # an existing session is reused
    capsys.readouterr()
    other = [("L09" if a == "L01" else a) for a in base]
    assert main(other) == 2
    run_dir = tmp_path / "runs" / "DEMO-cli-run"
    stimuli = th.ThresholdStimulusSet.read(stimuli_path)
    runner = _runner(run_layout(tmp_path / "runs", "DEMO-cli-run"), stimuli, session)
    with _client(serve_app, runner) as client:
        tr.run_bot_session(client, session, stimuli, max_trials=4)
    capsys.readouterr()
    assert (
        main(["export", "--run-dir", str(run_dir), "--out-dir", str(tmp_path / "ex"), "--no-plot"])
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["play_checks"][0]["ok"] and report["play_checks"][0]["n_answered"] == 4
    assert not (tmp_path / "ex" / "summary.png").exists()
    # a tampered set fails the check command
    data = json.loads(stimuli_path.read_text(encoding="utf-8"))
    data["pairs"][0]["sum_sq"] = "1/2"
    stimuli_path.write_text(json.dumps(data), encoding="utf-8")
    assert main(["check", "--stimuli", str(stimuli_path)]) == 1
    # a play log with a replay makes export fail
    plays = read_records(run_dir / "logs" / "plays.jsonl", PlayEvent)
    with open(run_dir / "logs" / "plays.jsonl", "ab") as handle:
        from av_generation.jsonio import canonical_line

        handle.write(canonical_line(plays[0].to_dict()))
    capsys.readouterr()
    assert (
        main(["export", "--run-dir", str(run_dir), "--out-dir", str(tmp_path / "ex2"), "--no-plot"])
        == 1
    )


def test_demo_command_through_the_real_server(tmp_path, capsys):
    """The synthetic example: DEMO set, two bot listeners through uvicorn, export and the
    example summary plot (labelled SYNTHETIC; uploaded as a CI artifact, never committed)."""
    out = CI_OUT / "demo" if os.environ.get("CI") else tmp_path / "demo"
    if out.exists():
        shutil.rmtree(out)
    assert main(["demo", "--out-dir", str(out), "--sessions", "2"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert [c["n_played"] for c in report["play_checks"]] == [224, 224]
    assert all(c["ok"] and c["n_refused"] == 0 for c in report["play_checks"])
    summary = json.loads((out / "export" / "summary.json").read_text(encoding="utf-8"))
    assert summary["set_sha256"] == th.ThresholdStimulusSet.read(DEMO_SET).sha256()
    assert summary["label"].startswith("SYNTHETIC")
    pooled = next(f for f in summary["fits"] if f["profile"] == "all")
    assert pooled["status"] == "ok" and 0.07 < pooled["d50"] < 0.13  # bot: d50 = 0.1
    assert (out / "export" / "summary.png").stat().st_size > 10_000
    again = run_demo(tmp_path / "again", sessions=1, small=True)
    assert again.play_checks[0].n_planned == 54 and again.play_checks[0].ok
    trials = th.read_trials_csv(out / "export" / "trials.csv")
    assert len(trials) == 448 and all(isinstance(t, ThresholdTrial) for t in trials)


# ---------------------------------------------------------------------------
# Browser: the real page in Chromium


@pytest.mark.browser
def test_listener_page_runs_a_session(tmp_path, serve_app, browser_page, tiny_set):
    small = dataclasses.replace(TINY, bin_centers=("0.175",), pairs_per_bin=1, same_pairs=1)
    stimuli = th.generate_stimuli("DEMO-T-page", small)
    layout = _open(tmp_path, stimuli, clock=SystemClock())
    session = _session(stimuli)
    runner = tr.ThresholdRunner(layout, stimuli, session, clock=SystemClock(), fsync=False)
    base = serve_app(tr.create_threshold_app(runner))
    page = browser_page
    page.goto(base + "/threshold/")
    page.wait_for_selector("body[data-state=ready]")
    assert page.text_content("#progress") == "Trial 1 of 2"
    for answer in ("#same", "#different"):
        page.click("#play")
        assert page.is_hidden("#play")  # no replay control during a trial
        page.wait_for_selector(f"{answer}:enabled", timeout=15_000)
        page.click(answer)
        page.wait_for_selector("body[data-state=ready], body[data-state=done]")
    page.wait_for_selector("body[data-state=done]")
    assert "complete" in page.text_content("#status")
    trials, plays = tr.read_run_records(layout)
    check = th.check_plays(session, stimuli, plays, trials)
    assert check.ok, check.problems
    assert [t.response for t in trials] == ["same", "different"]
    assert all(t.rt_ms is not None and t.onset_second_ms > t.onset_first_ms for t in trials)
    gap = trials[0].onset_second_ms - trials[0].onset_first_ms
    assert gap >= 500  # first motif + 500 ms gap (Web Audio schedule)
