"""Visit windows (check C7, endpoint timing, dashboard window adherence).

Windows count whole calendar days from an anchor visit of the same person (Protocol
constants; analysis plan section 2; Study A protocol section 6; Study B protocol sections
7 and 10): Study A D7 is day 7 +/- 1 after D0; Study B V2 is day 2 +/- 1 and V3 day 4 +/- 1
after V1 (V3 also after V2); W1 is 6-8 and W4 26-30 days after the person's own V3. The
first visit of each study is the anchor and has no window. The yoked Study B acquisition
session must start after its active session ended and within 24 h of the active start
(:func:`yoked_gap_ok`; proposed reading of "within 24 h", Study B protocol section 5.3).

A visit's date is the calendar date of its first run-sheet ``start_time`` in the UTC
offset recorded with it (``vocab.parse_timestamp``: ISO 8601 with an offset). Session
times must be timezone-aware: gaps are computed on absolute time, so a gap across a
daylight-saving change is exact, and naive datetimes are refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from .vocab import YOKED_MAX_HOURS, Timing


@dataclass(frozen=True)
class Window:
    """Allowed days after ``anchor`` (inclusive) and the visit that must come first."""

    study: str
    visit: str
    anchor: str
    lo_days: int
    hi_days: int
    after: str  # visit that must take place on an earlier date (B V3: V2)


WINDOWS: Final[dict[tuple[str, str], Window]] = {
    ("A", "D7"): Window("A", "D7", "D0", 6, 8, "D0"),
    ("B", "V2"): Window("B", "V2", "V1", 1, 3, "V1"),
    ("B", "V3"): Window("B", "V3", "V1", 3, 5, "V2"),
    ("B", "W1"): Window("B", "W1", "V3", 6, 8, "V3"),
    ("B", "W4"): Window("B", "W4", "V3", 26, 30, "V3"),
}
ANCHOR_VISITS: Final[dict[str, str]] = {"A": "D0", "B": "V1"}


def window(study: str, visit: str) -> Window | None:
    """The window of a visit, or None for the anchor visit (A D0, B V1)."""
    if ANCHOR_VISITS.get(study) == visit:
        return None
    try:
        return WINDOWS[(study, visit)]
    except KeyError:
        raise ValueError(f"unknown visit {study} {visit}") from None


def days_between(anchor: date, visit: date) -> int:
    """Whole calendar days from ``anchor`` to ``visit`` (negative if before)."""
    return (visit - anchor).days


def classify(study: str, visit: str, visit_date: date | None, anchor_date: date | None) -> Timing:
    """``in_window``, ``early`` or ``late``; ``not_applicable`` for an anchor visit;
    ``unknown`` when a date is missing."""
    w = window(study, visit)
    if w is None:
        return "not_applicable"
    if visit_date is None or anchor_date is None:
        return "unknown"
    days = days_between(anchor_date, visit_date)
    if days < w.lo_days:
        return "early"
    if days > w.hi_days:
        return "late"
    return "in_window"


def _require_aware(*values: datetime) -> None:
    for v in values:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError(f"naive datetime {v.isoformat()}: session times need a UTC offset")


def yoked_gap_hours(active_start: datetime, yoked_start: datetime) -> float:
    """Hours from the active session's start to the yoked session's start (aware times)."""
    _require_aware(active_start, yoked_start)
    return (yoked_start - active_start).total_seconds() / 3600.0


def yoked_gap_ok(active_start: datetime, active_end: datetime, yoked_start: datetime) -> bool:
    """True if the yoked session starts after the active one ended and within 24 h of its
    start. All three datetimes must be timezone-aware (``ValueError`` otherwise)."""
    _require_aware(active_start, active_end, yoked_start)
    if yoked_start < active_end:
        return False
    return yoked_gap_hours(active_start, yoked_start) <= YOKED_MAX_HOURS
