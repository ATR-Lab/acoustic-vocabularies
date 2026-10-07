"""Offline scoring only. Never imported by a participant view or console."""
from dataclasses import dataclass

ACTIONS = (
    ("ADD_ONE", "REMOVE_ONE", "FLIP_CARD", "ALIGN_ARROW"),
    ("SCAN", "TAG", "CLOSE", "QUARANTINE"),
)
TARGETS = (tuple("ABCD"), tuple("EFGH"))


@dataclass(frozen=True)
class CommandScore:
    response_code: str
    action_correct: bool
    referent_correct: bool
    exact_correct: bool


def legal(action, target):
    return any(action in actions and target in targets for actions, targets in zip(ACTIONS, TARGETS))


def score_command(expected_action, expected_target, response_code, response_action, response_target):
    """Distinguish absent responses from wrong legal commands; reject malformed rows."""
    if not legal(expected_action, expected_target):
        raise ValueError("SCORE_EXPECTED_TUPLE")
    if response_code not in ("commit", "dont_know", "timeout"):
        raise ValueError("SCORE_RESPONSE_CODE")
    if response_code != "commit":
        if response_action not in (None, "") or response_target not in (None, ""):
            raise ValueError("SCORE_ABSENT_RESPONSE_HAS_TUPLE")
        return CommandScore(response_code, False, False, False)
    if not legal(response_action, response_target):
        raise ValueError("SCORE_RESPONSE_TUPLE")
    action = response_action == expected_action
    target = response_target == expected_target
    return CommandScore(response_code, action, target, action and target)
