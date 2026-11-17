"""Every fault code the Unity app can emit has a reviewed fault type (#284).

The app writes its codes into ``technical_fault_code``. The codes are collected from the
runtime sources (``unity/Assets/ExperimentApp/Runtime/**/*.cs``): every string literal
matching ``app_fault_codes.APP_CODE_LITERAL_RE``, a superset of the fault codes (refusal
and status codes have the same shape). Each must be in ``vocab.APP_FAULT_CODE_TYPES`` (one
of the seven procedure fault types) or in ``app_fault_codes.APP_CODES_NOT_APPLICABLE``, so a
new app code fails here until it is classified.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from av_analysis.app_fault_codes import APP_CODE_LITERAL_RE, APP_CODES_NOT_APPLICABLE
from av_analysis.derive import fault_info
from av_analysis.scoring import is_technical_failure
from av_analysis.vocab import (
    APP_FAULT_CODE_TYPES,
    CANONICAL_FAULT_CODES,
    FAULT_CODE_RE,
    FAULT_CODE_TYPES,
    FAULT_TYPES,
    fault_type,
)

RUNTIME = Path(__file__).resolve().parents[2] / "unity" / "Assets" / "ExperimentApp" / "Runtime"


def _app_codes() -> tuple[str, ...]:
    literal = re.compile(APP_CODE_LITERAL_RE)
    codes: set[str] = set()
    for path in sorted(RUNTIME.rglob("*.cs")):
        codes.update(literal.findall(path.read_text(encoding="utf-8")))
    return tuple(sorted(codes))


APP_CODES = _app_codes()

# One app code per fault class of the procedure: frame monitor (freeze, response input),
# audio delivery deadline, package integrity, slot engine and assessment slot.
SEVEN_CLASSES = {
    "FRAME_FREEZE": "presentation_freeze",
    "FRAME_INTERFACE_UNAVAILABLE": "interface_loss",
    "AUDIO_UNDERRUN": "audio_underrun",
    "AUDIO_MISSING_PLAYBACK": "missing_playback",
    "HASH_MISMATCH": "hash_mismatch",
    "SESSION_RESET_DEADLINE_MISSED": "failed_reset",
    "ASSESSMENT_RESPONSE_LOG_MISSING": "missing_response_log",
}


def test_the_scan_finds_every_mapped_app_code():
    """REQ-INT-07: the runtime scan sees the app's codes, and each mapped code is one of them."""
    assert APP_CODES, f"no app codes found under {RUNTIME}"
    assert set(APP_FAULT_CODE_TYPES) <= set(APP_CODES)


@pytest.mark.parametrize("code", APP_CODES)
def test_every_app_code_is_classified(code):
    """REQ-INT-07: an app code maps to one of the seven fault types or is listed as not
    applicable (``other``); either way it makes its trial a technical failure."""
    assert re.fullmatch(FAULT_CODE_RE, code), "fault_type() and split_fault_codes() refuse it"
    assert code in APP_FAULT_CODE_TYPES or code in APP_CODES_NOT_APPLICABLE, (
        f"{code}: add it to vocab.APP_FAULT_CODE_TYPES (one of the seven fault types) or to "
        "app_fault_codes.APP_CODES_NOT_APPLICABLE"
    )
    expected = APP_FAULT_CODE_TYPES.get(code, "other")
    assert fault_type(code) == expected
    codes, types = fault_info({"technical_fault_code": code, "reset_ok": "true"})
    assert codes == (code,) and types == (expected,)
    assert is_technical_failure({"fault_codes": codes, "fault_types": types})


def test_app_codes_cover_the_seven_procedure_fault_types_apart():
    """REQ-INT-07: freeze, interface loss, underrun, missing playback, hash, reset and
    missing response log each have their own type, none of them ``other``."""
    types = [fault_type(code) for code in SEVEN_CLASSES]
    assert types == list(SEVEN_CLASSES.values())
    assert len(set(types)) == 7 and set(types) == set(FAULT_TYPES) - {"other"}
    assert set(APP_FAULT_CODE_TYPES.values()) == set(types)
    assert set(SEVEN_CLASSES) <= set(APP_CODES)


def test_the_app_mapping_agrees_with_the_canonical_codes():
    """REQ-INT-07: the app mapping neither overrides a canonical code nor lists a mapped code
    as not applicable."""
    canonical = {code: kind for kind, code in CANONICAL_FAULT_CODES.items()}
    for code, kind in APP_FAULT_CODE_TYPES.items():
        assert canonical.get(code, kind) == kind
        assert FAULT_CODE_TYPES[code] == kind
    assert not APP_CODES_NOT_APPLICABLE & set(FAULT_CODE_TYPES)
    assert all(re.fullmatch(FAULT_CODE_RE, code) for code in APP_CODES_NOT_APPLICABLE)
