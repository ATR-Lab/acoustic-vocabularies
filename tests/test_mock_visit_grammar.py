"""Synthetic grammar-stage histories; these cases are not native evidence."""
import pytest

from tools.mock_visit import native
from tools.mock_visit.records import EvidenceError

IDS = {"explanation": "1" * 32, "ready": "2" * 32, "clicks": "3" * 32}


def row(kind, phase=None, code=None):
    audio = None if code is None else dict(code=code, callback_count=1, delivered_samples=96001)
    return dict(payload=dict(kind=kind, phase=phase, audio_request_id=IDS.get(phase), audio=audio))


def play(phase):
    return [row("request", phase), row("audio", phase, "SIMULATION_DELIVERY_OBSERVED"),
            row("audio", phase, "AUDIO_PLAYBACK_COMPLETED"), row("completed", phase)]


def test_explanation_then_ready_then_clicks_is_the_only_complete_sequence():
    """REQ-GRAM-03: the explanation delivery is required before READY, the clicks and the finish."""
    rows = [row("started")] + play("explanation") + play("ready") + play("clicks") + [row("finished")]
    assert native.grammar(rows, set()) == set()
    with pytest.raises(EvidenceError, match="MOCK_GRAMMAR_PLAY_ORDER"):
        native.grammar([row("started")] + play("ready"), set())
    with pytest.raises(EvidenceError, match="MOCK_GRAMMAR_FINISH"):
        native.grammar([row("started")] + play("explanation") + play("ready") + [row("finished")], set())
    assert native.grammar([row("started")] + play("explanation"), set()) == {"GRAMMAR_COMPLETION_MISSING"}


def test_ready_cannot_overlap_an_unfinished_explanation():
    """REQ-GRAM-03: READY is not requested until the explanation playback has completed."""
    rows = [row("started"), row("request", "explanation"), row("audio", "explanation", "SIMULATION_DELIVERY_OBSERVED"), row("request", "ready")]
    with pytest.raises(EvidenceError, match="MOCK_GRAMMAR_OVERLAP"):
        native.grammar(rows, set())
