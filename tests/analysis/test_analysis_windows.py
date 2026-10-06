"""Visit windows (Protocol constants; Study A section 6; Study B sections 7 and 10)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from av_analysis.windows import (
    ANCHOR_VISITS,
    WINDOWS,
    classify,
    days_between,
    window,
    yoked_gap_hours,
    yoked_gap_ok,
)

D0 = date(2027, 3, 1)


def test_windows_match_the_protocol():
    expected = {
        ("A", "D7"): ("D0", 6, 8),
        ("B", "V2"): ("V1", 1, 3),
        ("B", "V3"): ("V1", 3, 5),
        ("B", "W1"): ("V3", 6, 8),
        ("B", "W4"): ("V3", 26, 30),
    }
    assert {k: (w.anchor, w.lo_days, w.hi_days) for k, w in WINDOWS.items()} == expected
    assert WINDOWS[("B", "V3")].after == "V2"
    assert window("A", "D0") is None and window("B", "V1") is None
    assert ANCHOR_VISITS == {"A": "D0", "B": "V1"}
    with pytest.raises(ValueError):
        window("A", "W1")


@pytest.mark.parametrize(
    ("study", "visit", "days", "timing"),
    [
        ("A", "D7", 5, "early"),
        ("A", "D7", 6, "in_window"),
        ("A", "D7", 8, "in_window"),
        ("A", "D7", 9, "late"),
        ("B", "W1", 6, "in_window"),
        ("B", "W1", 9, "late"),
        ("B", "W4", 25, "early"),
        ("B", "W4", 30, "in_window"),
        ("B", "W4", 31, "late"),
        ("B", "V2", 0, "early"),
    ],
)
def test_classify_boundaries(study, visit, days, timing):
    assert classify(study, visit, D0 + timedelta(days=days), D0) == timing


def test_classify_anchor_and_unknown():
    assert classify("A", "D0", D0, None) == "not_applicable"
    assert classify("B", "W1", None, D0) == "unknown"
    assert days_between(D0, D0 - timedelta(days=2)) == -2


@given(st.sampled_from(sorted(WINDOWS)), st.integers(min_value=-40, max_value=60))
def test_classify_is_consistent_with_the_bounds(key, days):
    w = WINDOWS[key]
    got = classify(*key, D0 + timedelta(days=days), D0)
    if days < w.lo_days:
        assert got == "early"
    elif days > w.hi_days:
        assert got == "late"
    else:
        assert got == "in_window"


def test_yoked_gap():
    start = datetime(2027, 3, 1, 10, 0)
    end = start + timedelta(minutes=75)
    assert yoked_gap_hours(start, start + timedelta(hours=23)) == 23.0
    assert yoked_gap_ok(start, end, start + timedelta(hours=24))
    assert not yoked_gap_ok(start, end, start + timedelta(hours=24, minutes=1))
    assert not yoked_gap_ok(start, end, start + timedelta(minutes=30))  # before active ended
