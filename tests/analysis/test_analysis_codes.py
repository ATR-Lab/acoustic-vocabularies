"""Reconciliation checks and discrepancy codes (shared by #33 and #35)."""

from __future__ import annotations

import re

import pytest

from av_analysis.codes import (
    CHECK_BY_ID,
    CHECKS,
    CODE_BY_ID,
    CODES,
    FAULT_INJECTIONS,
    SUSPENSION_EVENTS,
    SUSPENSION_TITLES,
    code,
    codes_of_check,
    suspension_event,
)


def test_eight_checks_in_order():
    assert [c.id for c in CHECKS] == [f"C{i}" for i in range(1, 9)]
    assert len({c.name for c in CHECKS}) == 8
    assert CHECK_BY_ID["C5"].studies == ("B",) and CHECK_BY_ID["C6"].studies == ("B",)
    for c in CHECKS:
        assert codes_of_check(c.id), f"{c.id} has no code"


def test_codes_are_unique_documented_and_uppercase():
    assert len(CODE_BY_ID) == len(CODES)
    for c in CODES:
        assert re.fullmatch(r"[A-Z][A-Z0-9_]+", c.code)
        assert c.check in CHECK_BY_ID
        assert c.title and c.description.endswith(".") and c.resolution.endswith(".")
    assert code("WINDOW_LATE").check == "C7"
    with pytest.raises(KeyError):
        code("NO_SUCH_CODE")


def test_fault_injection_suite_covers_the_issue_33_faults():
    assert set(FAULT_INJECTIONS) == {
        "missing_trial",
        "extra_play",
        "wrong_hash",
        "holdout_in_lesson",
        "changed_old_atom",
        "yoked_mismatch",
        "late_visit",
        "broken_retry_of",
        "unlinked_discrepancy",
    }
    for expected in FAULT_INJECTIONS.values():
        assert expected in CODE_BY_ID
    assert len(set(FAULT_INJECTIONS.values())) == len(FAULT_INJECTIONS)


def test_three_suspension_events_with_codes():
    assert set(SUSPENSION_TITLES) == {"WRONG_FILE_MAPPING", "ANSWER_LEAK", "OLD_WAVEFORM_CHANGED"}
    for event, codes in SUSPENSION_EVENTS.items():
        assert codes, event
        for c in codes:
            assert suspension_event(c) == event
    # The #35 acceptance faults raise red alerts.
    assert suspension_event(FAULT_INJECTIONS["wrong_hash"]) == "WRONG_FILE_MAPPING"
    assert suspension_event(FAULT_INJECTIONS["changed_old_atom"]) == "OLD_WAVEFORM_CHANGED"
    assert suspension_event("WINDOW_LATE") is None


def test_code_texts_hold_no_outcome_words():
    for c in CODES:
        text = f"{c.title} {c.description} {c.resolution}".lower()
        for word in ("accuracy", "score", "correct answer", "response time"):
            assert word not in text, (c.code, word)


def test_interface_document_lists_every_code():
    from pathlib import Path

    doc = Path(__file__).resolve().parents[2] / "docs" / "interfaces" / "analysis.md"
    text = doc.read_text(encoding="utf-8")
    for c in CODES:
        assert f"`{c.code}`" in text, c.code
