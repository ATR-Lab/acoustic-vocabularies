"""Response events, technical flags, playback status, retry placement and run integrity
(#33 review follow-up): each rule detects its injected fault, a deviation record naming
the trial explains it, and the clean logs never raise it."""

from __future__ import annotations

import argparse
import dataclasses
import shutil

import pytest

from av_analysis import reconcile
from av_analysis.fileio import csv_bytes, parse_csv, read_bytes
from av_analysis.paths import DataRoot, write_synthetic_input
from av_analysis.reconcile import reconcile_visit
from av_analysis.synthetic_logs import build_synthetic_root, refresh_exit_manifest, suite_visits

SEED = "DEMO-test-33-events"


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    return build_synthetic_root(
        tmp_path_factory.mktemp("events") / "base", seed_label=SEED, max_persons=1
    )


@pytest.fixture
def root(base, tmp_path):
    target = tmp_path / "root"
    shutil.copytree(base.path, target)
    return DataRoot.open(target)


@pytest.fixture
def visits(root):
    return suite_visits(root)


def read_rows(root, vid, name):
    header, records = parse_csv(read_bytes(root.raw_visit_dir(vid) / name))
    return list(header), [dict(zip(header, r, strict=True)) for r in records]


def write_rows(root, vid, name, header, rows):
    write_synthetic_input(
        root, "raw", f"{vid}/{name}", csv_bytes(header, ([r[c] for c in header] for r in rows))
    )
    refresh_exit_manifest(root, vid)


def add_deviation(root, vid, **fields):
    header, rows = read_rows(root, vid, "deviations.csv")
    row = dict.fromkeys(header, "")
    row.update(
        deviation_id=f"DEV-{len(rows) + 1}",
        timestamp="2027-03-01T12:00:00+01:00",
        protocol_version="v0.1",
        operator="S01",
        category="technical",
    )
    row.update(fields)
    write_rows(root, vid, "deviations.csv", header, [*rows, row])
    return row["deviation_id"]


def found(report, code=None):
    return [d for c in report.checks for d in c.discrepancies if code in (None, d.code)]


def codes(report):
    return sorted(d.code for d in found(report))


def mutate(root, vid, trial_type, change, *, nth=0):
    """Apply ``change(trial, plays)`` to the nth trial of a type and its linked plays."""
    th, trials = read_rows(root, vid, "trial-log.csv")
    lh, plays = read_rows(root, vid, "exposure-ledger.csv")
    trial = [t for t in trials if t["trial_type"] == trial_type and not t["retry_of"]][nth]
    change(trial, [p for p in plays if p["trial_ref"] == trial["trial_id"]])
    write_rows(root, vid, "trial-log.csv", th, trials)
    write_rows(root, vid, "exposure-ledger.csv", lh, plays)
    return trial["trial_id"]


def no_onset(trial, plays, *, code=""):
    trial.update(
        playback_status="confirmed_no_onset", exposure_consumed="false", technical_fault_code=code
    )
    for p in plays:
        p["audible_status"] = "confirmed_no_onset"


# ---------------------------------------------------------------------------------------
# Playback status against the exposure ledger (C4)


@pytest.mark.parametrize("visit_type", ["D0", "W1"])
def test_delivery_claimed_for_a_silent_play_is_a_playback_conflict(root, visits, visit_type):
    vid = visits[visit_type]

    def silent(trial, plays):
        for p in plays:
            p["audible_status"] = "confirmed_no_onset"

    tid = mutate(root, vid, "trained", silent)
    report = reconcile_visit(root, vid)
    hits = found(report, "PLAYBACK_STATUS_CONFLICT")
    assert [d.check for d in hits] == ["C4"] and hits[0].rows[0] == tid
    assert "observed_complete" in hits[0].detail and not report.passed
    add_deviation(root, vid, event_id=tid, category="audio")
    report = reconcile_visit(root, vid)
    assert all(d.resolved for d in found(report, "PLAYBACK_STATUS_CONFLICT")) and report.passed


@pytest.mark.parametrize(
    ("status", "audible", "conflict"),
    [
        ("confirmed_no_onset", "estimated", True),  # exposure understated
        ("not_requested", "confirmed_audible", True),  # plays without a request
        ("observed_complete", "estimated", False),  # callback-complete is delivered
        ("uncertain", "confirmed_audible", False),  # a conservative status is no conflict
    ],
)
def test_playback_status_rules(root, visits, status, audible, conflict):
    vid = visits["D7"]

    def change(trial, plays):
        trial.update(playback_status=status, technical_fault_code="AUDIO_UNDERRUN")
        for p in plays:
            p["audible_status"] = audible

    mutate(root, vid, "atomic", change)
    assert bool(found(reconcile_visit(root, vid), "PLAYBACK_STATUS_CONFLICT")) is conflict


# ---------------------------------------------------------------------------------------
# Response events (C2)


@pytest.mark.parametrize("visit_type", ["D0", "W4"])
def test_delivered_test_trial_without_a_response_event(root, visits, visit_type):
    vid = visits[visit_type]

    def clear(trial, plays):
        for column in (
            "response_code",
            "response_target",
            "response_action",
            "commit_mono_ms",
            "response_time_ms",
            "exact_correct",
            "action_correct",
            "referent_correct",
        ):
            trial[column] = ""

    tid = mutate(root, vid, "novel", clear)
    report = reconcile_visit(root, vid)
    hits = found(report, "RESPONSE_EVENT_MISSING")
    assert [(d.check, d.rows) for d in hits] == [("C2", (tid,))] and not report.passed
    assert "correct" not in hits[0].detail and "commit" in hits[0].detail
    add_deviation(root, vid, event_id=tid, category="procedure")
    assert reconcile_visit(root, vid).passed


def test_response_event_rules(root, visits):
    vid = visits["D7"]

    def commit_without_time(trial, plays):
        trial.update(response_code="commit", commit_mono_ms="", response_time_ms="")

    mutate(root, vid, "trained", commit_without_time)
    assert len(found(reconcile_visit(root, vid), "RESPONSE_EVENT_MISSING")) == 1

    def missing_log(trial, plays):
        trial.update(
            response_code="", commit_mono_ms="", technical_fault_code="MISSING_RESPONSE_LOG"
        )

    mutate(root, vid, "trained", missing_log, nth=1)  # a flagged missing log is no discrepancy
    assert len(found(reconcile_visit(root, vid), "RESPONSE_EVENT_MISSING")) == 1

    def no_cue_silent(trial, plays):
        trial.update(response_code="", commit_mono_ms="")

    mutate(root, vid, "no_cue", no_cue_silent)  # a no-cue trial still needs its response
    assert len(found(reconcile_visit(root, vid), "RESPONSE_EVENT_MISSING")) == 2

    def no_onset_without_response(trial, plays):
        no_onset(trial, plays, code="MISSING_PLAYBACK")
        trial.update(response_code="", commit_mono_ms="")

    mutate(root, vid, "atomic", no_onset_without_response)  # not delivered: no response
    report = reconcile_visit(root, vid)
    assert len(found(report, "RESPONSE_EVENT_MISSING")) == 2
    assert not found(report, "TECHNICAL_FLAG_MISSING")


# ---------------------------------------------------------------------------------------
# Technical flags (C2)


@pytest.mark.parametrize(
    ("change", "words"),
    [
        (lambda t, p: no_onset(t, p), "confirmed_no_onset"),
        (lambda t, p: t.update(playback_status="uncertain"), "uncertain"),
        (lambda t, p: t.update(frame_freeze_ms="300"), "frame freeze"),
        (lambda t, p: t.update(reset_ok="false"), "failed neutral reset"),
    ],
    ids=["no_onset", "uncertain", "freeze", "reset"],
)
@pytest.mark.parametrize("visit_type", ["D0", "V2"])
def test_fault_without_a_technical_flag(root, visits, visit_type, change, words):
    vid = visits[visit_type]
    tid = mutate(root, vid, "trained", change)
    report = reconcile_visit(root, vid)
    hits = found(report, "TECHNICAL_FLAG_MISSING")
    assert [(d.check, d.rows) for d in hits] == [("C2", (tid,))] and not report.passed
    assert words in hits[0].detail
    add_deviation(root, vid, event_id=vid, category="technical")  # visit-level record
    report = reconcile_visit(root, vid)
    assert all(d.resolved for d in found(report, "TECHNICAL_FLAG_MISSING"))


def test_flagged_faults_and_an_unrequested_cue(root, visits):
    vid = visits["D7"]

    def coded(trial, plays):
        trial.update(frame_freeze_ms="900", reset_ok="false", technical_fault_code="FRAME_FREEZE")

    mutate(root, vid, "trained", coded)
    assert not found(reconcile_visit(root, vid), "TECHNICAL_FLAG_MISSING")
    th, trials = read_rows(root, vid, "trial-log.csv")
    lh, plays = read_rows(root, vid, "exposure-ledger.csv")
    trial = next(t for t in trials if t["trial_type"] == "atomic")
    trial["playback_status"] = "not_requested"
    plays = [p for p in plays if p["trial_ref"] != trial["trial_id"]]
    write_rows(root, vid, "trial-log.csv", th, trials)
    write_rows(root, vid, "exposure-ledger.csv", lh, plays)
    report = reconcile_visit(root, vid)
    hits = found(report, "TECHNICAL_FLAG_MISSING")
    assert hits and "cue not requested" in hits[0].detail
    assert "COUNT_MISSING_PLAY" in codes(report)


# ---------------------------------------------------------------------------------------
# Retries at the end of their block (Common procedures section 6)


def _retry(root, vid, place):
    """A verified no-onset failure of the first trained trial and its retry, inserted at
    ``place(trials, original_index)``."""
    th, trials = read_rows(root, vid, "trial-log.csv")
    lh, plays = read_rows(root, vid, "exposure-ledger.csv")
    i = next(k for k, t in enumerate(trials) if t["trial_type"] == "trained")
    original = trials[i]
    mine = [p for p in plays if p["trial_ref"] == original["trial_id"]]
    no_onset(original, mine, code="MISSING_PLAYBACK")
    retry = {
        **original,
        "trial_id": original["trial_id"] + "-R1",
        "retry_of": original["trial_id"],
        "playback_status": "observed_complete",
        "exposure_consumed": "true",
        "technical_fault_code": "",
    }
    trials.insert(place(trials, i), retry)
    plays.append(
        {
            **mine[0],
            "event_id": "E-retry-1",
            "trial_ref": retry["trial_id"],
            "audible_status": "confirmed_audible",
        }
    )
    write_rows(root, vid, "trial-log.csv", th, trials)
    write_rows(root, vid, "exposure-ledger.csv", lh, plays)
    return retry["trial_id"]


def _block_end(trials, i):
    return max(k for k, t in enumerate(trials) if t["trial_type"] == "trained") + 1


@pytest.mark.parametrize("visit_type", ["D7", "W1"])
def test_retry_at_the_end_of_its_block_is_valid(root, visits, visit_type):
    vid = visits[visit_type]
    _retry(root, vid, _block_end)
    report = reconcile_visit(root, vid)
    assert codes(report) == [] and report.passed


@pytest.mark.parametrize(
    ("place", "why"),
    [
        (lambda trials, i: i + 1, "not at the end"),  # right after the failure, mid-block
        (lambda trials, i: _block_end(trials, i) + 1, "not at the end"),  # in the next block
    ],
    ids=["mid_block", "next_block"],
)
def test_retry_outside_the_end_of_its_block_is_broken(root, visits, place, why):
    vid = visits["D7"]
    retry = _retry(root, vid, place)
    hits = found(reconcile_visit(root, vid), "RETRY_LINK_BROKEN")
    assert len(hits) == 1 and retry in hits[0].rows and why in hits[0].detail


def test_retries_of_one_block_may_follow_each_other(root, visits):
    vid = visits["D7"]
    th, trials = read_rows(root, vid, "trial-log.csv")
    lh, plays = read_rows(root, vid, "exposure-ledger.csv")
    end = max(k for k, t in enumerate(trials) if t["trial_type"] == "trained") + 1
    originals = [t for t in trials if t["trial_type"] == "trained"][:2]
    for n, original in enumerate(originals, start=1):
        mine = [p for p in plays if p["trial_ref"] == original["trial_id"]]
        no_onset(original, mine, code="MISSING_PLAYBACK")
        retry = {
            **original,
            "trial_id": f"{original['trial_id']}-R1",
            "retry_of": original["trial_id"],
        }
        retry.update(
            playback_status="observed_complete", exposure_consumed="true", technical_fault_code=""
        )
        trials.insert(end + n - 1, retry)
        plays.append(
            {
                **mine[0],
                "event_id": f"E-retry-{n}",
                "trial_ref": retry["trial_id"],
                "audible_status": "confirmed_audible",
            }
        )
    write_rows(root, vid, "trial-log.csv", th, trials)
    write_rows(root, vid, "exposure-ledger.csv", lh, plays)
    assert codes(reconcile_visit(root, vid)) == []


def test_retry_of_another_item_is_broken(root, visits):
    vid = visits["D7"]
    retry = _retry(root, vid, _block_end)
    th, trials = read_rows(root, vid, "trial-log.csv")
    other = next(t for t in trials if t["trial_type"] == "trained" and t["trial_id"] != retry[:-3])
    next(t for t in trials if t["trial_id"] == retry)["message_id"] = other["message_id"]
    write_rows(root, vid, "trial-log.csv", th, trials)
    hits = found(reconcile_visit(root, vid), "RETRY_LINK_BROKEN")
    assert hits and "another item" in hits[0].detail


# ---------------------------------------------------------------------------------------
# Answer display with a test play (C4) and run integrity (C1)


def test_feedback_content_with_a_test_play_is_an_answer_leak(root, visits):
    vid = visits["D7"]

    def feedback(trial, plays):
        plays[0]["feedback_content_id"] = "FB-x"

    mutate(root, vid, "trained", feedback)
    report = reconcile_visit(root, vid)
    hits = found(report, "ANSWER_DISPLAY_LEAK")
    assert len(hits) == 1 and hits[0].rows[0].startswith("E")
    assert report.document()["summary"]["suspension_events"] == ["ANSWER_LEAK"]


def test_a_raw_file_changed_during_the_run_fails_the_visit(root, visits, monkeypatch, capsys):
    vid = visits["D0"]
    clean = reconcile_visit(root, vid)
    assert clean.passed and not dataclasses.replace(clean, raw_unchanged=False).passed
    add_deviation(root, vid, event_id=vid, category="technical")  # cannot explain it
    original = reconcile.load_references_with
    calls = []

    def load_and_touch(data_root, visit_id, reader):
        calls.append(visit_id)
        write_synthetic_input(data_root, "raw", f"{vid}/late-{len(calls)}.csv", b"a\n1\n")
        return original(data_root, visit_id, reader)

    monkeypatch.setattr(reconcile, "load_references_with", load_and_touch)
    report = reconcile_visit(root, vid)
    assert report.raw_unchanged is False and report.passed is False
    hits = found(report, "RAW_HASH_CHANGED")
    assert [(d.check, d.rows, d.resolved) for d in hits] == [
        ("C1", (f"raw/{vid}/late-1.csv",), False)
    ]
    doc = report.document()
    assert doc["summary"]["status"] == "fail" and doc["raw_unchanged"] is False
    args = argparse.Namespace(root=str(root.path), visit_ids=[vid], all=False)
    assert reconcile.main(args) == 1
    assert "failed C1" in capsys.readouterr().out
