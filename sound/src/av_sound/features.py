"""The 12-feature vector and the exact separation metric (Study A protocol §3.2).

Features are exact `Fraction`s, so `separated()` compares
`sum((x_j - y_j)^2)` with `12 * threshold^2` without rounding. The answer at a
boundary is therefore the same on every platform. `distance()` returns a float for
reports and tools only; admissibility never depends on it.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from decimal import Decimal
from fractions import Fraction
from typing import TypeAlias

from av_sound.recipe import Recipe

Features: TypeAlias = tuple[Fraction, ...]
"""Twelve exact values in [0, 1], in `FEATURE_NAMES` order."""

FeatureLike: TypeAlias = Recipe | Sequence[Fraction | int]
"""A recipe, or a 12-value feature vector of `Fraction` or `int`."""

ThresholdLike: TypeAlias = Fraction | Decimal | int | str
"""A threshold: a `Fraction`, a `Decimal`, an `int` or a decimal string such as `"0.10"`."""

N_FEATURES = 12
FEATURE_NAMES: tuple[str, ...] = (
    "pitch_1",
    "pitch_2",
    "pitch_3",
    "total",
    "proportion_1",
    "proportion_2",
    "proportion_3",
    "gap_1",
    "gap_2",
    "amplitude_1",
    "amplitude_2",
    "amplitude_3",
)

# Normalization constants, written as in the protocol: (x - low) / (high - low).
_PITCH_LOW, _PITCH_SPAN = Fraction(-6), Fraction(12)
_TOTAL_LOW, _TOTAL_SPAN = Fraction(450), Fraction(450)
_PROPORTION_LOW = Fraction(1, 9)
_PROPORTION_SPAN = Fraction(2, 3) - Fraction(1, 9)
_GAP_LOW, _GAP_SPAN = Fraction(20), Fraction(40)
_AMPLITUDE_LOW, _AMPLITUDE_SPAN = Fraction(3, 5), Fraction(2, 5)

_DECIMAL = re.compile(r"(0|[1-9][0-9]*)(\.[0-9]+)?")


def _amplitude(value: float) -> Fraction:
    # Recipe amplitudes are exactly the floats 0.6, 0.8 or 1.0; their shortest repr is
    # the decimal the protocol means, so Fraction(repr(a)) is exact (3/5, 4/5, 1).
    return Fraction(repr(value))


def features(recipe: Recipe) -> Features:
    """The 12 normalized features of a recipe, as exact fractions in [0, 1].

    Order: three pitches `(p+6)/12`, total `(T-450)/450`, three proportions
    `(q-1/9)/(2/3-1/9)` with `q = w/sum(w)`, two gaps `(g-20)/40`, three amplitudes
    `(a-0.6)/0.4`.
    """
    if not isinstance(recipe, Recipe):
        raise TypeError(f"features() expects a Recipe, got {type(recipe).__name__}")
    weight_sum = sum(recipe.rhythm_weights)
    return (
        *((p - _PITCH_LOW) / _PITCH_SPAN for p in recipe.pitches),
        (recipe.total_ms - _TOTAL_LOW) / _TOTAL_SPAN,
        *(
            (Fraction(w, weight_sum) - _PROPORTION_LOW) / _PROPORTION_SPAN
            for w in recipe.rhythm_weights
        ),
        *((g - _GAP_LOW) / _GAP_SPAN for g in recipe.gaps_ms),
        *((_amplitude(a) - _AMPLITUDE_LOW) / _AMPLITUDE_SPAN for a in recipe.amplitudes),
    )


def as_features(value: FeatureLike) -> Features:
    """Features of a recipe, or a checked copy of a 12-value vector of `Fraction`/`int`."""
    if isinstance(value, Recipe):
        return features(value)
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise TypeError("expected a Recipe or a sequence of 12 Fraction/int values")
    if len(value) != N_FEATURES:
        raise ValueError(f"expected {N_FEATURES} feature values, got {len(value)}")
    out: list[Fraction] = []
    for v in value:
        if isinstance(v, bool) or not isinstance(v, Fraction | int):
            raise TypeError(f"feature values must be Fraction or int (exact), got {v!r}")
        out.append(Fraction(v))
    return tuple(out)


def sum_squared_diff(a: FeatureLike, b: FeatureLike) -> Fraction:
    """Exact `sum((x_j - y_j)^2)` over the 12 features."""
    fa, fb = as_features(a), as_features(b)
    return sum(((x - y) ** 2 for x, y in zip(fa, fb, strict=True)), Fraction(0))


def distance(a: FeatureLike, b: FeatureLike) -> float:
    """`sqrt(sum((x_j - y_j)^2) / 12)` as a float, for reports and tools.

    The exact sum is converted once (correctly rounded), so the float is the same on
    every platform. Admissibility uses `separated()`, never this value.
    """
    return distance_from_sum_sq(sum_squared_diff(a, b))


def distance_from_sum_sq(sum_sq: Fraction) -> float:
    """`sqrt(sum_sq / 12)` as a float: one correctly rounded conversion, then `math.sqrt`."""
    return math.sqrt(float(sum_sq / N_FEATURES))


def parse_threshold(value: ThresholdLike) -> Fraction:
    """An exact, non-negative threshold. Floats are refused: pass `"0.10"`, not `0.1`."""
    if isinstance(value, bool | float):
        raise TypeError(f"threshold must be exact (str, Fraction, Decimal or int), got {value!r}")
    if isinstance(value, str):
        if not _DECIMAL.fullmatch(value):
            raise ValueError(f"threshold {value!r} is not a non-negative decimal like '0.10'")
        result = Fraction(value)
    elif isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"threshold {value!r} is not finite")
        result = Fraction(value)
    elif isinstance(value, Fraction | int):
        result = Fraction(value)
    else:
        raise TypeError(f"unsupported threshold type {type(value).__name__}")
    if result < 0:
        raise ValueError(f"threshold must be >= 0, got {value!r}")
    return result


def separation_limit(threshold: ThresholdLike) -> Fraction:
    """`12 * threshold^2`: the smallest admissible `sum_squared_diff`."""
    t = parse_threshold(threshold)
    return N_FEATURES * t * t


def separated(a: FeatureLike, b: FeatureLike, threshold: ThresholdLike) -> bool:
    """True when the distance is at least `threshold`, decided exactly.

    Equivalent to `distance(a, b) >= threshold` with exact arithmetic:
    `sum((x_j - y_j)^2) >= 12 * threshold^2`.
    """
    return sum_squared_diff(a, b) >= separation_limit(threshold)


def format_fraction(value: Fraction) -> str:
    """Exact text: a plain decimal when the value has one (`"0.1"`), else `"p/q"`."""
    den = value.denominator
    twos = fives = 0
    while den % 2 == 0:
        den //= 2
        twos += 1
    while den % 5 == 0:
        den //= 5
        fives += 1
    if den != 1:
        return str(value)
    places = max(twos, fives)
    scaled = value * 10**places
    assert scaled.denominator == 1
    sign = "-" if scaled < 0 else ""
    digits = str(abs(scaled.numerator)).rjust(places + 1, "0")
    if places == 0:
        return sign + digits
    return f"{sign}{digits[:-places]}.{digits[-places:]}"
