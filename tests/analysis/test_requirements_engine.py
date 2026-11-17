"""Requirement trace (engine group): analysis-side scoring, repeat marking and
non-resumed batteries. Synthetic DEMO rows only."""

from __future__ import annotations

import itertools
import json
import shutil
from fractions import Fraction

import pytest

from av_analysis.derive import derive_tables
from av_analysis.derived import TRIALS, row_problems
from av_analysis.fileio import csv_bytes, parse_csv, read_bytes
from av_analysis.loaders import raw_visit_ids
from av_analysis.paths import DataRoot, write_synthetic_input
from av_analysis.reconcile import reconcile_many, write_report
from av_analysis.scoring import battery_scores, score_trial
from av_analysis.synthetic_logs import (
    EXIT_MANIFEST,
    build_synthetic_root,
    exit_manifest_bytes,
    inject_fault,
    refresh_exit_manifest,
    suite_visits,
)
from av_analysis.vocab import RESPONSE_CODES

SEED = "DEMO-trace-engine"
ACTIONS = (
    ("ADD_ONE", "REMOVE_ONE", "FLIP_CARD", "ALIGN_ARROW"),
    ("SCAN", "TAG", "CLOSE", "QUARANTINE"),
)
TARGETS = (tuple("ABCD"), tuple("EFGH"))
# Every legal response tuple of the panel: each target with its family's four actions.
LEGAL = tuple(
    (action, target)
    for actions, targets in zip(ACTIONS, TARGETS, strict=True)
    for target in targets
    for action in actions
)


@pytest.fixture
def trial(sample_row):
    def make(**overrides):
        base = dict(
            trial_type="trained",
            block="trained",
            retry_of=None,
            row_source="logged",
            family="K",
            target_action="ADD_ONE",
            target_referent="A",
            response_code="commit",
            response_action="ADD_ONE",
            response_target="A",
            exact_correct=True,
            fault_codes=(),
            fault_types=(),
            valid_delivery=True,
            audio_onset_estimate_mono_ms=1000,
            commit_mono_ms=4000,
            response_time_ms=3000,
            playback_status="observed_complete",
        )
        base.update(overrides)
        row = sample_row(TRIALS, **base)
        assert row_problems(TRIALS, row) == []
        return row

    return make


def _family(target):
    return "K" if target in TARGETS[0] else "Q"


def test_only_matching_target_and_action_scores_correct(trial):
    """REQ-SCORE-01: over every request and every legal committed tuple, Y = 1 exactly
    when both the target and the action equal the request."""
    for (want_action, want_target), (got_action, got_target) in itertools.product(LEGAL, LEGAL):
        row = trial(
            family=_family(want_target),
            target_action=want_action,
            target_referent=want_target,
            response_action=got_action,
            response_target=got_target,
            exact_correct=(want_action, want_target) == (got_action, got_target),
        )
        y = score_trial(row).y_operational
        assert y == int(got_action == want_action and got_target == want_target)


def test_chance_for_a_committed_guess_is_one_in_32(trial):
    """REQ-SCORE-02: the panel's response space is 8 targets x 4 family actions = 32 legal
    tuples, and for every request exactly one of them scores correct, so a uniform
    committed guess scores 1/32."""
    assert len(LEGAL) == 32 and len(set(LEGAL)) == 32
    assert {t for _, t in LEGAL} == set("ABCDEFGH")
    for target in "ABCDEFGH":
        assert sum(1 for _, t in LEGAL if t == target) == 4
    for want_action, want_target in LEGAL:
        scores = [
            score_trial(
                trial(
                    family=_family(want_target),
                    target_action=want_action,
                    target_referent=want_target,
                    response_action=got_action,
                    response_target=got_target,
                    exact_correct=(want_action, want_target) == (got_action, got_target),
                )
            ).y_operational
            for got_action, got_target in LEGAL
        ]
        assert Fraction(sum(scores), len(scores)) == Fraction(1, 32)


def test_incorrect_abstained_and_timed_out_all_score_zero_with_separate_codes(trial):
    """REQ-SCORE-03: a wrong commit, an abstention (dont_know) and a timeout all score 0,
    and abstention and timeout remain two distinct logged codes."""
    assert {"dont_know", "timeout"} <= set(RESPONSE_CODES)
    wrong = trial(response_action="REMOVE_ONE", exact_correct=False)
    rows = {"commit": wrong}
    for code in ("dont_know", "timeout"):
        rows[code] = trial(
            response_code=code,
            response_action=None,
            response_target=None,
            exact_correct=None,
            commit_mono_ms=None,
        )
    for code, row in rows.items():
        score = score_trial(row)
        assert score.y_operational == 0 and score.y_valid == 0, code
        assert row["response_code"] == code
    assert rows["dont_know"]["response_code"] != rows["timeout"]["response_code"]


# ---------------------------------------------------------------------------------------
# Synthetic visit roots


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    return build_synthetic_root(
        tmp_path_factory.mktemp("trace-engine") / "base", seed_label=SEED, max_persons=1
    )


@pytest.fixture
def root(base, tmp_path):
    target = tmp_path / "root"
    shutil.copytree(base.path, target)
    return DataRoot.open(target)


def _read(root, vid, name):
    header, records = parse_csv(read_bytes(root.raw_visit_dir(vid) / name))
    return list(header), [dict(zip(header, r, strict=True)) for r in records]


def _write(root, vid, name, header, rows):
    data = csv_bytes(header, ([r[c] for c in header] for r in rows))
    write_synthetic_input(root, "raw", f"{vid}/{name}", data)
    refresh_exit_manifest(root, vid)


def _reconcile(root):
    for report in reconcile_many(root, raw_visit_ids(root)):
        write_report(root, report)


def test_later_play_of_a_heard_reserved_message_is_marked_repeat(root):
    """REQ-RSV-06: after a reserved (held-out) message was heard on its novel trial, a later
    logged play of the same message is derived as a repeat (never a first exposure)."""
    vid = suite_visits(root)["D7"]
    th, trials = _read(root, vid, "trial-log.csv")
    lh, plays = _read(root, vid, "exposure-ledger.csv")
    novel = next(r for r in trials if r["trial_type"] == "novel")
    play = next(p for p in plays if p["trial_ref"] == novel["trial_id"])
    later = {**novel, "trial_id": novel["trial_id"] + "-R1", "retry_of": novel["trial_id"]}
    later_play = {**play, "event_id": "E-trace-later", "trial_ref": later["trial_id"]}
    last = max(k for k, r in enumerate(trials) if r["trial_type"] == "novel")
    trials.insert(last + 1, later)
    plays.append(later_play)
    _write(root, vid, "trial-log.csv", th, trials)
    _write(root, vid, "exposure-ledger.csv", lh, plays)
    _reconcile(root)
    rows = {t["trial_id"]: t for t in derive_tables(root)["trials"] if t["visit_id"] == vid}
    assert rows[novel["trial_id"]]["novelty"] == "first"
    repeat = rows[later["trial_id"]]
    assert repeat["novelty"] == "repeat" and repeat["prior_phrase_exposures"] >= 1


def test_battery_not_resumed_records_missing_endpoint_and_keeps_committed_scores(root):
    """REQ-INT-04: a battery stopped by an interruption and not resumed (closed
    ``interrupted``, unplayed trial unaccounted) gives a missing endpoint score with a
    technical-stop reason, while the committed trials keep their scores."""
    vid = suite_visits(root)["D7"]
    inject_fault(root, vid, "missing_trial")  # last trained trial never played
    folder = root.raw_visit_dir(vid)
    manifest = json.loads(read_bytes(folder / EXIT_MANIFEST))
    files = {p.name: read_bytes(p) for p in sorted(folder.iterdir()) if p.is_file()}
    data = exit_manifest_bytes(vid, manifest["session_id"], files, closed="interrupted")
    write_synthetic_input(root, "raw", f"{vid}/{EXIT_MANIFEST}", data)
    _reconcile(root)
    tables = derive_tables(root)
    endpoint = next(
        e for e in tables["endpoints"] if e["visit_id"] == vid and e["battery"] == "trained"
    )
    assert endpoint["status"] == "partial" and endpoint["missing_reason"] == "technical_stop"
    assert endpoint["accounted_n"] == endpoint["scheduled_n"] - 1
    assert endpoint["planned_endpoint"] is False
    mine = [t for t in tables["trials"] if t["visit_id"] == vid and t["block"] == "trained"]
    score = next(
        s for s in battery_scores(mine, [endpoint]) if s.family is None
    )
    assert score.operational is None and score.valid_delivery is None
    kept = sum(score_trial(t).y_operational for t in mine if t["retry_of"] is None)
    assert score.operational_n == len(mine) and score.operational_sum == kept > 0
