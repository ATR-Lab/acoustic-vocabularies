"""12-feature vector, exact distance and the separation boundary (issue #9).

No pair of domain recipes is at distance exactly 0.10 (proof by exhaustive
enumeration below), so the exact-boundary acceptance check runs through the exact
comparison with constructed feature vectors, and through the nearest achievable
in-domain pairs on both sides. Thresholds that are exactly achievable (0.125, 0.15,
0.25) are tested with real recipe pairs.
"""

from __future__ import annotations

import bisect
import functools
import importlib
import itertools
import json
import math
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from av_sound import (
    FEATURE_NAMES,
    Profile,
    Recipe,
    Reference,
    distance,
    features,
    parse_threshold,
    render,
    separated,
    sum_squared_diff,
    validate,
)
from av_sound.features import as_features, format_fraction, separation_limit
from av_sound.recipe import AMPLITUDES, GAPS_MS, PITCHES, RHYTHM_WEIGHTS, TOTAL_MS

validate_mod = importlib.import_module("av_sound.validate")
BOUNDARY = (
    Path(__file__).resolve().parents[2] / "sound" / "testvectors" / "validator" / "boundary.json"
)
WEIGHTS = list(itertools.product(RHYTHM_WEIGHTS, repeat=3))
ZERO = (Fraction(0),) * 12
TENTH = (Fraction(1, 10),) * 12  # sum of squares 12/100 = 3/25: distance exactly 0.10

recipes = st.builds(
    Recipe,
    total_ms=st.sampled_from(TOTAL_MS),
    pitches=st.tuples(*[st.sampled_from(PITCHES)] * 3),
    rhythm_weights=st.tuples(*[st.sampled_from(RHYTHM_WEIGHTS)] * 3),
    gaps_ms=st.tuples(*[st.sampled_from(GAPS_MS)] * 2),
    amplitudes=st.tuples(*[st.sampled_from(AMPLITUDES)] * 3),
)


# --- feature definition ------------------------------------------------------------------


def test_feature_formulas_on_the_worked_example():
    r = Recipe(600, (-3, 0, 4), (2, 1, 3), (40, 20), (1.0, 0.6, 0.8))
    q = [Fraction(2, 6), Fraction(1, 6), Fraction(3, 6)]
    expected = (
        Fraction(3, 12),
        Fraction(6, 12),
        Fraction(10, 12),
        Fraction(150, 450),
        *((x - Fraction(1, 9)) / (Fraction(2, 3) - Fraction(1, 9)) for x in q),
        Fraction(20, 40),
        Fraction(0),
        Fraction(1),
        Fraction(0),
        Fraction(1, 2),
    )
    assert features(r) == expected
    assert len(FEATURE_NAMES) == 12
    assert all(type(f) is Fraction for f in features(r))


def test_all_features_in_unit_interval_for_every_domain_extreme():
    """Every timing structure (4 x 64 x 9) with pitch and amplitude extremes."""
    pitch_extremes = [(-6, -6, -6), (6, 6, 6), (-6, 6, -6), (6, -6, 0)]
    amp_extremes = [(0.6, 0.6, 0.6), (1.0, 1.0, 1.0), (0.6, 1.0, 0.8), (1.0, 0.6, 1.0)]
    lows, highs = [Fraction(1)] * 12, [Fraction(0)] * 12
    count = 0
    for total, weights, gaps in itertools.product(
        TOTAL_MS, WEIGHTS, itertools.product(GAPS_MS, repeat=2)
    ):
        for pitches, amps in zip(pitch_extremes, amp_extremes, strict=True):
            f = features(Recipe(total, pitches, weights, gaps, amps))
            assert all(0 <= x <= 1 for x in f)
            lows = [min(a, b) for a, b in zip(lows, f, strict=True)]
            highs = [max(a, b) for a, b in zip(highs, f, strict=True)]
            count += 1
    assert count == 2304 * 4
    assert lows == [0] * 12 and highs == [1] * 12  # every coordinate reaches both ends


def _f(total=750, pitches=(0, 0, 0), weights=(2, 2, 2), gaps=(40, 40), amps=(0.8,) * 3):
    return features(Recipe(total, pitches, weights, gaps, amps))


def test_each_field_maps_its_whole_domain_onto_unit_interval():
    for p in PITCHES:
        assert _f(pitches=(p, p, p))[:3] == (Fraction(p + 6, 12),) * 3
    assert [_f(total=t)[3] for t in TOTAL_MS] == [0, Fraction(1, 3), Fraction(2, 3), 1]
    props = {x for w in WEIGHTS for x in _f(weights=w)[4:7]}
    assert min(props) == 0 and max(props) == 1
    assert _f(weights=(1, 4, 4))[4] == 0 and _f(weights=(4, 1, 1))[4] == 1
    for g, expected in zip(GAPS_MS, (0, Fraction(1, 2), 1), strict=True):
        assert _f(gaps=(g, g))[7:9] == (expected,) * 2
    for a, expected in zip(AMPLITUDES, (0, Fraction(1, 2), 1), strict=True):
        assert _f(amps=(a,) * 3)[9:] == (expected,) * 3


def test_feature_vectors_are_checked():
    assert as_features(ZERO) == ZERO
    assert as_features([0] * 12) == ZERO
    with pytest.raises(ValueError):
        as_features([0] * 11)
    with pytest.raises(TypeError):
        as_features([0.0] * 12)
    with pytest.raises(TypeError):
        as_features([True] * 12)
    with pytest.raises(TypeError):
        as_features("0" * 12)
    with pytest.raises(TypeError):
        features(Recipe(750, (0,) * 3, (2, 2, 2), (40, 40), (0.8,) * 3).to_dict())


# --- distance ----------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(a=recipes, b=recipes, c=recipes)
def test_distance_properties(a, b, c):
    assert distance(a, a) == 0 and sum_squared_diff(a, a) == 0
    assert distance(a, b) == distance(b, a)
    assert sum_squared_diff(a, b) == sum_squared_diff(b, a)
    assert 0 <= distance(a, b) <= 1
    assert distance(a, b) == math.sqrt(float(sum_squared_diff(a, b) / 12))
    assert distance(a, c) <= distance(a, b) + distance(b, c) + 1e-12
    assert sum_squared_diff(a, b) == sum_squared_diff(features(a), features(b))


@settings(max_examples=200, deadline=None)
@given(a=recipes, b=recipes, num=st.integers(0, 400))
def test_separated_is_the_exact_threshold_comparison(a, b, num):
    t = Fraction(num, 1000)
    assert separated(a, b, t) == (sum_squared_diff(a, b) >= 12 * t * t)
    assert separated(a, b, t) == separated(b, a, t)
    if separated(a, b, t):
        assert all(separated(a, b, Fraction(k, 1000)) for k in range(0, num + 1, 25))


# --- threshold parsing ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0.10", Fraction(1, 10)),
        ("0.1", Fraction(1, 10)),
        ("0", Fraction(0)),
        ("1", Fraction(1)),
        ("0.125", Fraction(1, 8)),
        (Decimal("0.10"), Fraction(1, 10)),
        (Fraction(3, 20), Fraction(3, 20)),
        (0, Fraction(0)),
    ],
)
def test_parse_threshold(value, expected):
    assert parse_threshold(value) == expected


@pytest.mark.parametrize("value", ["-0.1", "0.1.0", ".1", "1/10", "1e-1", " 0.1", "", "nan"])
def test_parse_threshold_rejects_non_decimal_text(value):
    with pytest.raises(ValueError):
        parse_threshold(value)


@pytest.mark.parametrize("value", [0.1, True, None, [0.1]])
def test_parse_threshold_rejects_inexact_types(value):
    with pytest.raises(TypeError):
        parse_threshold(value)


def test_parse_threshold_rejects_bad_decimals():
    with pytest.raises(ValueError):
        parse_threshold(Decimal("NaN"))
    with pytest.raises(ValueError):
        parse_threshold(Fraction(-1, 10))


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (Fraction(1, 10), "0.1"),
        (Fraction(1, 8), "0.125"),
        (Fraction(0), "0"),
        (Fraction(3), "3"),
        (Fraction(1, 3), "1/3"),
        (Fraction(-1, 4), "-0.25"),
        (Fraction(1, 100), "0.01"),
    ],
)
def test_format_fraction(value, text):
    assert format_fraction(value) == text
    assert Fraction(text) == value


# --- the 0.10 boundary -----------------------------------------------------------------------


def test_constructed_vectors_exactly_at_threshold_pass_and_just_below_fail():
    assert sum_squared_diff(ZERO, TENTH) == Fraction(3, 25) == separation_limit("0.10")
    assert separated(ZERO, TENTH, "0.10")
    assert separated(TENTH, ZERO, Fraction(1, 10))
    just_below = (Fraction(1, 10) - Fraction(1, 10**12), *TENTH[1:])
    assert not separated(ZERO, just_below, "0.10")
    just_above = (Fraction(1, 10) + Fraction(1, 10**12), *TENTH[1:])
    assert separated(ZERO, just_above, "0.10")
    # A float would not decide this case reliably; the exact comparison does.
    assert distance(ZERO, just_below) == pytest.approx(0.1, abs=1e-12)


def test_validate_decides_the_exact_boundary(monkeypatch):
    """End to end through validate(): patched features give distance exactly 0.10."""
    reference = Recipe(900, (0, 0, 0), (1, 2, 3), (20, 20), (0.6, 0.8, 1.0))
    candidate = Recipe(900, (1, 0, 0), (1, 2, 3), (20, 20), (0.6, 0.8, 1.0))
    vectors = {reference: ZERO, candidate: TENTH}
    monkeypatch.setattr(validate_mod, "features", lambda r: vectors[r])
    committed = [Reference.from_rendered("K-a1", render(reference, Profile.P1))]
    result = validate(candidate, Profile.P1, committed, reserved=(), threshold="0.10")
    assert result.ok and result.nearest_distance == pytest.approx(0.1)
    vectors[candidate] = (Fraction(1, 10) - Fraction(1, 10**12), *TENTH[1:])
    result = validate(candidate, Profile.P1, committed, reserved=(), threshold="0.10")
    assert result.codes == ("E_SEPARATION",)


@functools.cache
def _achievable_sums() -> tuple[frozenset[Fraction], list[Fraction]]:
    """Exact squared sums between domain recipes, as P (weights) and R (other fields).

    The proportion features depend jointly on the weights; each other feature depends
    on one field, so every achievable sum is p + r for p in P and r in R and vice versa.
    """
    props = {w: _f(weights=w)[4:7] for w in WEIGHTS}
    p_set = frozenset(
        sum(((x - y) ** 2 for x, y in zip(props[a], props[b], strict=True)), Fraction(0))
        for a in WEIGHTS
        for b in WEIGHTS
    )

    def squares(values):
        return {(x - y) ** 2 for x in values for y in values}

    pitch = squares({_f(pitches=(p, p, p))[0] for p in PITCHES})
    total = squares({_f(total=t)[3] for t in TOTAL_MS})
    gap = squares({_f(gaps=(g, g))[7] for g in GAPS_MS})
    amp = squares({_f(amps=(a,) * 3)[9] for a in AMPLITUDES})
    r_set = {Fraction(0)}
    for coordinate in [pitch, pitch, pitch, total, gap, gap, amp, amp, amp]:
        r_set = {s + d for s in r_set for d in coordinate}
    return p_set, sorted(r_set)


def _neighbours(target: Fraction) -> tuple[bool, Fraction, Fraction]:
    p_set, r_list = _achievable_sums()
    exact, below, above = False, Fraction(-1), Fraction(10**9)
    for p in p_set:
        i = bisect.bisect_left(r_list, target - p)
        if i < len(r_list) and p + r_list[i] == target:
            exact = True
            i_above = i + 1
        else:
            i_above = i
        if i > 0:
            below = max(below, p + r_list[i - 1])
        if i_above < len(r_list):
            above = min(above, p + r_list[i_above])
    return exact, below, above


def test_no_domain_pair_is_exactly_at_0_10_and_fixtures_are_the_nearest():
    exact, below, above = _neighbours(Fraction(3, 25))
    assert not exact  # proof by exhaustive enumeration of every achievable squared sum
    assert below == Fraction(10789, 90000) and above == Fraction(10589, 88200)
    doc = json.loads(BOUNDARY.read_text(encoding="utf-8"))
    pairs = {p["name"]: p for p in doc["pairs"]}
    assert Fraction(pairs["t0.10-below"]["sum_sq"]) == below
    assert Fraction(pairs["t0.10-above"]["sum_sq"]) == above
    assert "t0.10-exact" not in pairs


@pytest.mark.parametrize("text", ["0.05", "0.125", "0.15", "0.20", "0.25"])
def test_other_thresholds_match_the_fixture_analysis(text):
    doc = json.loads(BOUNDARY.read_text(encoding="utf-8"))
    analysis = {a["threshold"]: a for a in doc["analysis"]}[text]
    exact, below, above = _neighbours(separation_limit(text))
    assert exact == analysis["exact_achievable"]
    assert below == Fraction(analysis["below_sum_sq"])
    assert above == Fraction(analysis["above_sum_sq"])


def _boundary_pairs():
    return json.loads(BOUNDARY.read_text(encoding="utf-8"))["pairs"]


@pytest.mark.parametrize("pair", _boundary_pairs(), ids=lambda p: p["name"])
def test_boundary_fixture(pair):
    reference = Recipe.from_dict(pair["reference"])
    candidate = Recipe.from_dict(pair["candidate"])
    profile = Profile(pair["profile"])
    threshold = pair["threshold"]
    s = sum_squared_diff(candidate, reference)
    assert s == Fraction(pair["sum_sq"])
    assert distance(candidate, reference) == pair["distance"]
    limit = separation_limit(threshold)
    assert {"below": s < limit, "exact": s == limit, "above": s > limit}[pair["relation"]]
    assert separated(candidate, reference, threshold) is pair["separated"]
    rendered = [render(r, profile) for r in (reference, candidate)]
    assert not any(r.short_event for r in rendered)
    assert rendered[0].pcm_sha256 != rendered[1].pcm_sha256
    committed = [Reference.from_rendered("REF", rendered[0])]
    result = validate(candidate, profile, committed, reserved=(), threshold=threshold)
    assert list(result.codes) == pair["codes"]
    assert result.ok is pair["separated"]


def test_boundary_fixtures_cover_both_sides_at_every_threshold():
    pairs = _boundary_pairs()
    for text in ("0.05", "0.10", "0.125", "0.15", "0.20", "0.25"):
        relations = {p["relation"] for p in pairs if p["threshold"] == text}
        assert {"below", "above"} <= relations
    assert {p["threshold"] for p in pairs if p["relation"] == "exact"} == {"0.125", "0.15", "0.25"}
