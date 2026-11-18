"""Requirement trace (audio group): fault classification of the codes the app emits.

The Unity app writes one bounded code per apparatus fault into ``technical_fault_code``.
These tests feed the exact producer codes into the analysis fault classifier and
check that each fault class keeps its own type.
"""

from __future__ import annotations

import pytest

from av_analysis.vocab import FAULT_TYPES, fault_type

# Producer codes emitted by the Unity runtime on main, one per fault class:
# AudioDelivery.DeadlineFault (underrun / missing playback), PcmWave and the package
# loader (hash), FrameMonitor (freeze over 250 ms, response interface unavailable),
# FixedSlotEngine (reset deadline) and the assessment slot (missing response log).
APP_UNDERRUN = "AUDIO_UNDERRUN"
APP_MISSING_PLAYBACK = "AUDIO_MISSING_PLAYBACK"
APP_HASH = "HASH_MISMATCH"
APP_FREEZE = "FRAME_FREEZE"
APP_INTERFACE_LOST = "FRAME_INTERFACE_UNAVAILABLE"
APP_FAILED_RESET = "SESSION_RESET_DEADLINE_MISSED"
APP_MISSING_RESPONSE_LOG = "ASSESSMENT_RESPONSE_LOG_MISSING"


def test_classifier_keeps_underrun_hash_freeze_and_reset_codes_apart():
    """REQ-INT-07: app underrun, hash, freeze and reset codes get four distinct types."""
    types = {
        APP_UNDERRUN: fault_type(APP_UNDERRUN),
        APP_HASH: fault_type(APP_HASH),
        APP_FREEZE: fault_type(APP_FREEZE),
        APP_FAILED_RESET: fault_type(APP_FAILED_RESET),
    }
    assert types == {
        APP_UNDERRUN: "audio_underrun",
        APP_HASH: "hash_mismatch",
        APP_FREEZE: "presentation_freeze",
        APP_FAILED_RESET: "failed_reset",
    }


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Requirement gap: AUDIO_MISSING_PLAYBACK, FRAME_INTERFACE_UNAVAILABLE and "
        "ASSESSMENT_RESPONSE_LOG_MISSING classify as 'other'; no response-interface type exists"
    ),
)
def test_classifier_separates_all_seven_app_fault_classes():
    """REQ-INT-07: freeze, interface loss, underrun, missing playback, hash, reset and missing
    response log each map to a distinct type that is not 'other'."""
    codes = (
        APP_FREEZE,
        APP_INTERFACE_LOST,
        APP_UNDERRUN,
        APP_MISSING_PLAYBACK,
        APP_HASH,
        APP_FAILED_RESET,
        APP_MISSING_RESPONSE_LOG,
    )
    types = [fault_type(code) for code in codes]
    assert "other" not in types
    assert len(set(types)) == len(codes)
    assert fault_type(APP_MISSING_PLAYBACK) == "missing_playback"
    assert fault_type(APP_MISSING_RESPONSE_LOG) == "missing_response_log"
    assert all(t in FAULT_TYPES for t in types)
