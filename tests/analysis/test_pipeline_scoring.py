"""#34 scoring: Y, valid-delivery score, components, censored time to commit, batteries."""

from __future__ import annotations

import pytest

from av_analysis.derived import ENDPOINTS, TRIALS, row_problems
from av_analysis.scoring import (
    ScoringError,
    battery_scores,
    is_opportunity,
    score_trial,
    scored_opportunities,
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


def test_exact_first_commit_scores_one(trial):
    s = score_trial(trial())
    assert (s.y_operational, s.y_valid, s.action_correct, s.referent_correct) == (1, 1, 1, 1)
    assert (s.time_to_commit_ms, s.censored) == (3000, False)


@pytest.mark.parametrize(
    ("overrides", "action", "referent"),
    [
        ({"response_action": "REMOVE_ONE"}, 0, 1),
        ({"response_target": "B"}, 1, 0),
        ({"response_action": "FLIP_CARD", "response_target": "C"}, 0, 0),
    ],
)
def test_wrong_components_score_zero(trial, overrides, action, referent):
    s = score_trial(trial(**overrides))
    assert s.y_operational == 0 and s.y_valid == 0
    assert (s.action_correct, s.referent_correct) == (action, referent)


@pytest.mark.parametrize("code", ["dont_know", "timeout"])
def test_abstention_and_timeout_are_unsuccessful_and_censored(trial, code):
    s = score_trial(
        trial(response_code=code, response_action=None, response_target=None, commit_mono_ms=None)
    )
    assert s.y_operational == 0 and (s.action_correct, s.referent_correct) == (0, 0)
    assert (s.time_to_commit_ms, s.censored) == (12_000, True)


def test_technical_failure_is_zero_operationally_and_excluded_from_valid(trial):
    # A correct commit on a faulted row still scores 0 (plan section 2).
    s = score_trial(
        trial(
            fault_codes=("AUDIO_UNDERRUN",), fault_types=("audio_underrun",), valid_delivery=False
        )
    )
    assert s.y_operational == 0 and s.y_valid is None and s.time_to_commit_ms is None
    lost = trial(
        row_source="deviation",
        response_code=None,
        response_action=None,
        response_target=None,
        exact_correct=None,
        fault_codes=("OPPORTUNITY_LOST",),
        fault_types=("other",),
        valid_delivery=False,
        playback_status=None,
        audio_onset_estimate_mono_ms=None,
        commit_mono_ms=None,
        response_time_ms=None,
    )
    s = score_trial(lost)
    assert s.y_operational == 0 and s.y_valid is None and s.action_correct == 0


def test_uncertain_delivery_without_fault_is_scored_but_not_valid(trial):
    s = score_trial(trial(valid_delivery=False, playback_status="uncertain"))
    assert s.y_operational == 1 and s.y_valid is None


def test_no_response_recorded_is_zero(trial):
    s = score_trial(
        trial(response_code=None, response_action=None, response_target=None, commit_mono_ms=None)
    )
    assert s.y_operational == 0 and s.time_to_commit_ms is None and not s.censored


def test_atomic_probe_scores_the_probed_role(trial):
    row = trial(
        trial_type="atomic",
        block="atomic",
        target_action=None,
        target_referent="C",
        response_action=None,
        response_target="C",
    )
    s = score_trial(row)
    assert s.y_operational == 1 and s.action_correct is None and s.referent_correct == 1
    late = score_trial(
        trial(
            trial_type="atomic",
            block="atomic",
            target_action="FLIP_CARD",
            target_referent=None,
            response_action="FLIP_CARD",
            response_target=None,
            commit_mono_ms=9000,
        )
    )
    assert late.y_operational == 1 and (late.time_to_commit_ms, late.censored) == (7000, True)


def test_late_commit_is_censored_at_twelve_seconds(trial):
    s = score_trial(trial(commit_mono_ms=14_000))
    assert (s.time_to_commit_ms, s.censored) == (12_000, True)
    logged = score_trial(trial(audio_onset_estimate_mono_ms=None, response_time_ms=2500))
    assert logged.time_to_commit_ms == 2500  # falls back to the logged response time
    none = score_trial(trial(audio_onset_estimate_mono_ms=None, response_time_ms=None))
    assert none.time_to_commit_ms is None


def test_no_cue_has_no_acoustic_time(trial):
    s = score_trial(trial(trial_type="no_cue", block="validity", item_id=None))
    assert s.y_operational == 1 and s.time_to_commit_ms is None


def test_lessons_and_retries_are_not_opportunities(trial):
    lesson = trial(trial_type="message_lesson", block="message_lessons", response_code=None)
    retry = trial(retry_of="A-C01-L01-D0-TR-01")
    for row in (lesson, retry):
        assert not is_opportunity(row)
        assert score_trial(row).y_operational is None


def test_malformed_rows_are_refused(trial):
    with pytest.raises(ScoringError):
        score_trial(trial(target_action=None, target_referent=None))
    row = trial()
    row["valid_delivery"] = None
    with pytest.raises(ScoringError):
        score_trial(row)
    row = trial()
    row["commit_mono_ms"] = "4000"
    with pytest.raises(ScoringError):
        score_trial(row)


def _battery(trial, sample_row, n, *, wrong=(), faulted=(), status="complete", accounted=None):
    rows = []
    for i in range(n):
        fam = "K" if i % 2 == 0 else "Q"
        over = {"trial_id": f"T-{i:02d}", "family": fam}
        if fam == "Q":
            over.update(
                target_action="SCAN",
                target_referent="E",
                response_action="SCAN",
                response_target="E",
            )
        if i in wrong:
            over["response_target"] = "B" if fam == "K" else "F"
        if i in faulted:
            over.update(
                fault_codes=("MISSING_PLAYBACK",),
                fault_types=("missing_playback",),
                valid_delivery=False,
            )
        rows.append(trial(**over))
    endpoint = sample_row(
        ENDPOINTS,
        battery="trained",
        scheduled_n=36,
        accounted_n=n if accounted is None else accounted,
        status=status,
        missing_reason=None if status == "complete" else "withdrawn_mid_battery",
    )
    return rows, endpoint


def test_battery_score_mean_of_36_with_family_denominators(trial, sample_row):
    rows, endpoint = _battery(trial, sample_row, 36, wrong=(0, 1, 2), faulted=(4,))
    scores = battery_scores(rows, [endpoint])
    overall = next(s for s in scores if s.family is None)
    assert overall.scheduled_n == 36 and overall.operational_n == 36 and overall.valid_n == 35
    assert overall.operational == pytest.approx(32 / 36)
    assert overall.valid_delivery == pytest.approx(32 / 35)
    k = next(s for s in scores if s.family == "K")
    q = next(s for s in scores if s.family == "Q")
    assert (k.scheduled_n, q.scheduled_n) == (18, 18)
    assert k.operational == pytest.approx(15 / 18) and q.operational == pytest.approx(17 / 18)
    assert (k.operational + q.operational) / 2 == pytest.approx(overall.operational)


def test_partial_battery_keeps_its_known_contribution(trial, sample_row):
    rows, endpoint = _battery(trial, sample_row, 20, wrong=(0,), status="partial")
    overall = next(s for s in battery_scores(rows, [endpoint]) if s.family is None)
    assert overall.operational is None and overall.valid_delivery is None
    assert (overall.operational_n, overall.operational_sum) == (20, 19.0)


def test_battery_scores_refuse_inconsistent_tables(trial, sample_row):
    rows, endpoint = _battery(trial, sample_row, 36, accounted=35, status="partial")
    with pytest.raises(ScoringError, match="accounted_n"):
        battery_scores(rows, [endpoint])
    rows, endpoint = _battery(trial, sample_row, 36)
    with pytest.raises(ScoringError, match="without an endpoints row"):
        battery_scores(rows, [])
    rows, endpoint = _battery(trial, sample_row, 36)
    for r in rows:
        r["family"] = "K"
    with pytest.raises(ScoringError, match="K opportunities"):
        battery_scores(rows, [endpoint])
    rows, endpoint = _battery(trial, sample_row, 20)
    endpoint["status"] = "complete"
    with pytest.raises(ScoringError, match="complete but"):
        battery_scores(rows, [endpoint])
    assert len(scored_opportunities(rows)) == 20
