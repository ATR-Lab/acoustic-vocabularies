"""Raw-log values aligned with the provisional producer (#72), fault types, staff IDs."""

from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from av_analysis import vocab
from av_analysis.vocab import (
    AUDIBLE_STATUS,
    CANONICAL_FAULT_CODES,
    CONSUMING_AUDIBLE_STATUS,
    FAULT_CODE_RE,
    FAULT_CODE_TYPES,
    FAULT_TYPES,
    LOST_OPPORTUNITY_CODE,
    PLAYBACK_STATUS,
    RESPONSE_CODES,
    STAFF_ID_RE,
    fault_type,
    split_fault_codes,
)


def test_values_follow_the_provisional_producer():
    # data-csv-provisional-1 (docs/data/README.md on main): trial playback summary,
    # exposure audible status, response codes without a "none" value.
    assert set(PLAYBACK_STATUS) == {
        "observed_complete",
        "uncertain",
        "confirmed_no_onset",
        "not_requested",
    }
    assert set(AUDIBLE_STATUS) == {
        "confirmed_audible",
        "estimated",
        "uncertain",
        "confirmed_no_onset",
    }
    assert set(CONSUMING_AUDIBLE_STATUS) == set(AUDIBLE_STATUS) - {"confirmed_no_onset"}
    assert RESPONSE_CODES == ("commit", "dont_know", "timeout")


def test_fault_codes_map_to_the_six_protocol_types_or_other():
    assert FAULT_TYPES[:6] == (
        "audio_underrun",
        "missing_playback",
        "hash_mismatch",
        "failed_reset",
        "missing_response_log",
        "presentation_freeze",
    )
    assert FAULT_TYPES[-1] == "other"
    for kind, code in CANONICAL_FAULT_CODES.items():
        assert re.fullmatch(FAULT_CODE_RE, code)
        assert fault_type(code) == kind
    assert set(CANONICAL_FAULT_CODES) == set(FAULT_TYPES) - {"other"}
    assert fault_type("HASH_MISMATCH") == "hash_mismatch"  # package-format.md section 4
    assert fault_type("FRAME_FREEZE") == "presentation_freeze"
    assert fault_type("DATA_ONSET_EVIDENCE_CONFLICT") == "other"
    assert fault_type(LOST_OPPORTUNITY_CODE) == "other"
    assert set(FAULT_CODE_TYPES.values()) <= set(FAULT_TYPES)
    for bad in ("audio_underrun", "", "A-B", "1ABC"):
        with pytest.raises(ValueError):
            fault_type(bad)


def test_split_fault_codes():
    assert split_fault_codes("") == ()
    assert split_fault_codes("AUDIO_UNDERRUN") == ("AUDIO_UNDERRUN",)
    assert split_fault_codes("DATA_RESPONSE_CONFLICT;FRAME_FREEZE") == (
        "DATA_RESPONSE_CONFLICT",
        "FRAME_FREEZE",
    )
    for bad in (";", "AUDIO_UNDERRUN;", "audio_underrun", "A B"):
        with pytest.raises(ValueError):
            split_fault_codes(bad)


@given(st.lists(st.from_regex(r"[A-Z][A-Z0-9_]{0,20}", fullmatch=True), min_size=1, max_size=4))
def test_split_fault_codes_round_trips(codes):
    assert split_fault_codes(";".join(codes)) == tuple(codes)


def test_staff_ids_are_coded():
    assert re.fullmatch(STAFF_ID_RE, "S03") and re.fullmatch(STAFF_ID_RE, "RA12")
    for bad in ("Jane Doe", "s03", "S3", "staff-03"):
        assert not re.fullmatch(STAFF_ID_RE, bad)


def test_waveform_hash_kinds():
    assert vocab.WAVEFORM_HASH_KINDS == ("file", "pcm")
