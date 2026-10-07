"""Common selector (#20): hand-computed fixtures, rules and properties (Study A §3.3)."""

import json
import random
from fractions import Fraction
from pathlib import Path

import pytest
from av_sound.recipe import Profile
from hypothesis import given, settings
from hypothesis import strategies as st

from av_generation.ids import Method, Study, proposal_slot_id, round_and_slot
from av_generation.outcomes import SlotOutcome
from av_generation.records import CandidateScore, RatingRecord, SlotRecord
from av_generation.selector import (
    SelectorError,
    format_score,
    parse_score,
    pick_incumbent,
    score_candidate,
)

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads(
    (ROOT / "tests/generation/fixtures/orchestrator/selector-cases.json").read_text("utf-8")
)["cases"]
BOOK = "DEMO-BK-7QX4"
SEATS = ("R01", "R02", "R03")


def _slot(index: int, valid: bool = True) -> SlotRecord:
    round_, slot = round_and_slot(index)
    return SlotRecord(
        run_id="DEMO-sel-01",
        study=Study.A,
        method=Method.A2,
        slot_id=proposal_slot_id(BOOK, "K-a1", round_, slot),
        profile=Profile.P1,
        atom_id="K-a1",
        slot=slot,
        slot_index=index,
        outcome=SlotOutcome.VALID if valid else SlotOutcome.EVENT_TOO_SHORT,
        t_open_ms=0,
        t_ms=0,
        batch_id="DEMO-A-P01",
        book_id=BOOK,
        round=round_,
    )


def _rating(slot: SlotRecord, seat: int, value, *, first_atom: bool, placeholder: bool = False):
    round_, slot_no = round_and_slot(slot.slot_index)
    base = dict(
        run_id="DEMO-sel-01",
        batch_id="DEMO-A-P01",
        book_id=BOOK,
        atom_id="K-a1",
        round=round_,
        position=slot_no,
        rating_slot_id=f"DEMO-A-P01.K-a1.r{round_}p{slot_no}",
        slot_id=slot.slot_id,
        rater_id=SEATS[seat],
        station=f"S{seat + 1}",
        rater_kind="bot",
        placeholder=placeholder,
        first_atom=first_atom,
        reconnected=False,
        slot_start_ms=0,
        t_ms=20_000,
    )
    if placeholder:
        return RatingRecord(
            **base,
            association=None,
            distinguishability=None,
            distinguishability_by_rule=False,
            comfort=None,
            missing=False,
        )
    if value is None:
        return RatingRecord(
            **base,
            association=None,
            distinguishability=None,
            distinguishability_by_rule=False,
            comfort=None,
            missing=True,
        )
    association, distinguishability, comfort = value
    return RatingRecord(
        **base,
        association=association,
        distinguishability=distinguishability,
        distinguishability_by_rule=False,
        comfort=comfort,
        missing=False,
    )


def _scores(case) -> list[CandidateScore]:
    out = []
    for cand in case["candidates"]:
        slot = _slot(cand["slot_index"], cand["valid"])
        if cand["valid"]:
            ratings = [
                _rating(slot, i, v, first_atom=case["first_atom"])
                for i, v in enumerate(cand["ratings"])
            ]
        else:
            ratings = [
                _rating(slot, i, None, first_atom=case["first_atom"], placeholder=True)
                for i in range(3)
            ]
        out.append(score_candidate(slot, ratings, first_atom=case["first_atom"]))
    return out


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_hand_computed_fixtures(case):
    scores = _scores(case)
    expected = case["expected"]
    assert [s.eligible for s in scores] == expected["eligible"]
    assert [s.score for s in scores] == expected["scores"]
    assert [s.flagged_missing for s in scores] == expected["flagged"]
    slot_id, score = pick_incumbent(scores)
    if expected["incumbent"] is None:
        assert (slot_id, score) == (None, None)
    else:
        winner = next(s for s in scores if s.slot_index == expected["incumbent"])
        assert slot_id == winner.slot_id
        assert score == Fraction(expected["score"])


def test_fixture_suite_covers_the_acceptance_cases():
    names = " ".join(c["name"] for c in CASES)
    assert "one of three comfort-acceptable" in names
    assert "lowest submission slot" in names
    assert "first atom" in names
    assert len(CASES) >= 10


def test_counts_and_invalid_candidates():
    slot = _slot(1)
    ratings = [
        _rating(slot, 0, (5, 5, "acceptable"), first_atom=False),
        _rating(slot, 1, (6, 2, "unacceptable"), first_atom=False),
        _rating(slot, 2, None, first_atom=False),
    ]
    cand = score_candidate(slot, ratings, first_atom=False)
    assert (cand.n_ratings, cand.n_acceptable, cand.score_raters) == (2, 1, 2)
    assert cand.score == "9/2" and not cand.eligible and cand.flagged_missing
    bad = _slot(2, valid=False)
    placeholders = [_rating(bad, i, None, first_atom=False, placeholder=True) for i in range(3)]
    cand = score_candidate(bad, placeholders, first_atom=False)
    assert (cand.technically_valid, cand.n_ratings, cand.score, cand.eligible) == (
        False,
        0,
        None,
        False,
    )
    # an invalid candidate is never scored, even if a rating record slipped in
    leaked = [_rating(bad, 0, (7, 7, "acceptable"), first_atom=False)] * 3
    assert score_candidate(bad, leaked, first_atom=False).score is None


def test_ratings_of_another_slot_are_refused():
    slot, other = _slot(1), _slot(2)
    with pytest.raises(SelectorError):
        score_candidate(
            slot, [_rating(other, 0, (4, 4, "acceptable"), first_atom=False)], first_atom=False
        )


def test_score_text_round_trip():
    for value in (Fraction(9, 2), Fraction(4), Fraction(7, 6), Fraction(0)):
        assert parse_score(format_score(value)) == value
    for text in ("4/2", "-1", "08", "1/1"):
        with pytest.raises((SelectorError, ValueError)):
            parse_score(text)
    with pytest.raises(SelectorError):
        format_score(Fraction(-1, 2))


rating_values = st.one_of(
    st.none(),
    st.tuples(
        st.integers(1, 7), st.integers(1, 7), st.sampled_from(["acceptable", "unacceptable"])
    ),
)


@settings(max_examples=150, deadline=None)
@given(
    st.lists(
        st.tuples(st.booleans(), st.lists(rating_values, min_size=3, max_size=3)), max_size=12
    ),
    st.booleans(),
    st.randoms(use_true_random=False),
)
def test_selector_matches_a_brute_force_reference(cands, first_atom, rnd):
    indices = rnd.sample(range(1, 13), len(cands))
    scores = []
    reference = []
    for index, (valid, values) in zip(indices, cands, strict=True):
        slot = _slot(index, valid)
        ratings = [_rating(slot, i, v, first_atom=first_atom) for i, v in enumerate(values)]
        cand = score_candidate(slot, ratings, first_atom=first_atom)
        scores.append(cand)
        rated = [v for v in values if v is not None] if valid else []
        halves = [Fraction(a + (4 if first_atom else d), 2) for a, d, _ in rated]
        mean = sum(halves, Fraction(0)) / len(halves) if halves else None
        acceptable = sum(1 for *_, c in rated if c == "acceptable")
        assert cand.score == (None if mean is None else str(mean))
        assert cand.eligible == (valid and mean is not None and acceptable >= 2)
        assert cand.flagged_missing == (valid and len(halves) < 3)
        if cand.eligible:
            reference.append((mean, -index, cand.slot_id))
    expected = max(reference)[2] if reference else None
    slot_id, score = pick_incumbent(scores)
    assert slot_id == expected
    assert (score is None) == (expected is None)
    shuffled = list(scores)
    random.Random(len(scores)).shuffle(shuffled)
    assert pick_incumbent(shuffled) == (slot_id, score)
