import itertools
import pytest
from analysis.command_scoring import ACTIONS, TARGETS, legal, score_command


TUPLES = [(a, t) for aa, tt in zip(ACTIONS, TARGETS) for a, t in itertools.product(aa, tt)]


@pytest.mark.parametrize("action,target", TUPLES)
def test_all32_exact_wrong_components_and_distinct_absence(action, target):
    exact = score_command(action, target, "commit", action, target)
    assert exact.exact_correct and exact.action_correct and exact.referent_correct
    group = next(i for i, values in enumerate(TARGETS) if target in values)
    wrong_action = next(a for a in ACTIONS[group] if a != action)
    wrong_target = next(t for t in TARGETS[group] if t != target)
    a = score_command(action, target, "commit", wrong_action, target)
    t = score_command(action, target, "commit", action, wrong_target)
    assert (a.exact_correct, a.action_correct, a.referent_correct) == (False, False, True)
    assert (t.exact_correct, t.action_correct, t.referent_correct) == (False, True, False)
    for code in ("dont_know", "timeout"):
        result = score_command(action, target, code, None, None)
        assert result.response_code == code and not any((result.exact_correct, result.action_correct, result.referent_correct))


@pytest.mark.parametrize("code,action,target", [("commit", "SCAN", "A"), ("commit", None, "A"),
                                              ("timeout", "ADD_ONE", "B"), ("dont_know", None, "B"),
                                              ("unknown", None, None)])
def test_malformed_rows_are_not_silently_scored_zero(code, action, target):
    with pytest.raises(ValueError, match="SCORE_"):
        score_command("ADD_ONE", "B", code, action, target)


def test_illegal_answer_key_is_refused():
    with pytest.raises(ValueError, match="SCORE_EXPECTED_TUPLE"):
        score_command("SCAN", "B", "commit", "ADD_ONE", "B")
    assert len(TUPLES) == 32 and len(set(TUPLES)) == 32
    assert not legal("ADD_ONE", "TRAY_B")  # No implicit label normalization.
