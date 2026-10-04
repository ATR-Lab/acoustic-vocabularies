"""Lookup tables of renderer spec D2 and D4, generated with exact integer arithmetic.

No floating-point `sin`, `cos` or `pow` is used, so the tables are identical on every
platform. Their SHA-256 digests are fixed in the spec and checked at import time.
"""

from __future__ import annotations

import hashlib

import numpy as np
import numpy.typing as npt

from av_sound.recipe import PITCHES, Profile

SAMPLE_RATE = 48_000
SAMPLES_PER_MS = 48
SINE_BITS = 16
SINE_SIZE = 1 << SINE_BITS
SINE_Q = 24
ENV_Q = 30
ENV_ONE = 1 << ENV_Q
ATTACK_SAMPLES = 480
RELEASE_SAMPLES = 1440
PHASE_BITS = 32
HARMONICS: tuple[int, ...] = (1, 2, 3)

SINE_SHA256 = "7507c6a534ec1b3bb8bd7e2650f4dad6e0d04adf3d0000cbb2472985b49d2b21"
ATTACK_SHA256 = "7fbc9783264314af81e078b6759a67170904a55f935f207f0ced7c50b3a3af34"
RELEASE_SHA256 = "2b2b6c3486da35d3a73cacb83ae1d45bdc5ce9a2d7b7eaeb6cbfe4b36858d3ec"
INCREMENT_SHA256 = "14864688cdbb9cacbd6f633485b15c902a7416f0e38a2b8c2616c5b809bdd5f0"

_PREC = 96  # fixed-point bits used while generating the tables

IntArray = npt.NDArray[np.int64]


def _pi_fixed(prec: int) -> int:
    """pi in Q(prec), from Machin's formula with 16 guard bits."""
    guard = prec + 16
    one = 1 << guard

    def atan_inv(x: int) -> int:
        total = term = one // x
        x2 = x * x
        k = 1
        sign = -1
        while term:
            term //= x2
            total += sign * (term // (2 * k + 1))
            sign = -sign
            k += 1
        return total

    return (16 * atan_inv(5) - 4 * atan_inv(239)) >> 16


_PI = _pi_fixed(_PREC)


def _sin_quarter(num: int, den: int) -> int:
    """sin(pi/2 * num/den) in Q(_PREC) for 0 <= num <= den (Taylor series)."""
    one = 1 << _PREC
    x = (_PI * num) // (2 * den)
    x2 = (x * x) >> _PREC
    term = total = x
    k = 1
    while term:
        term = -((term * x2) >> _PREC) // ((2 * k) * (2 * k + 1))
        total += term
        k += 1
    return max(0, min(total, one))


def _round_shift(value: int, shift: int) -> int:
    """Round half up of value / 2^shift (value >= 0)."""
    return (value + (1 << (shift - 1))) >> shift


def _build_sine() -> IntArray:
    quarter_n = SINE_SIZE // 4
    quarter = [
        _round_shift(_sin_quarter(i, quarter_n), _PREC - SINE_Q) for i in range(quarter_n + 1)
    ]
    table = np.zeros(SINE_SIZE, dtype=np.int64)
    for i, v in enumerate(quarter):
        table[i] = v
        table[2 * quarter_n - i] = v
        if i:
            table[2 * quarter_n + i] = -v
            table[SINE_SIZE - i] = -v
    table[2 * quarter_n] = 0
    return table


def _build_raised_cosine(length: int) -> IntArray:
    """(1 - cos(pi k / length)) / 2 in Q30 for k = 0..length, i.e. sin^2(pi k / (2 length))."""
    values = []
    for k in range(length + 1):
        s = _sin_quarter(k, length)
        values.append(_round_shift((s * s) >> _PREC, _PREC - ENV_Q))
    return np.array(values, dtype=np.int64)


def _iroot(value: int, n: int) -> int:
    """floor(value ** (1/n)) for a non-negative integer."""
    x = 1 << ((value.bit_length() + n - 1) // n)
    while True:
        y = ((n - 1) * x + value // x ** (n - 1)) // n
        if y >= x:
            break
        x = y
    while x**n > value:
        x -= 1
    while (x + 1) ** n <= value:
        x += 1
    return x


def _build_increments() -> IntArray:
    """INC[profile, pitch + 6, harmonic - 1] = round(h f0 2^(p/12) 2^32 / 48000)."""
    k = _PREC
    out = np.zeros((len(Profile), len(PITCHES), len(HARMONICS)), dtype=np.int64)
    for a, profile in enumerate(Profile):
        for b, pitch in enumerate(PITCHES):
            ratio = _iroot(1 << (pitch + 12 * k), 12)  # floor(2^(k + p/12))
            for c, h in enumerate(HARMONICS):
                num = h * profile.f0_hz * ratio * (1 << PHASE_BITS)
                den = SAMPLE_RATE * (1 << k)
                out[a, b, c] = (2 * num + den) // (2 * den)
    return out


def _digest(array: IntArray, dtype: str) -> str:
    return hashlib.sha256(np.ascontiguousarray(array, dtype=dtype).tobytes()).hexdigest()


def _frozen(array: IntArray) -> IntArray:
    array.setflags(write=False)
    return array


SINE: IntArray = _frozen(_build_sine())
ATTACK: IntArray = _frozen(_build_raised_cosine(ATTACK_SAMPLES))
RELEASE: IntArray = _frozen(_build_raised_cosine(RELEASE_SAMPLES))
INCREMENTS: IntArray = _frozen(_build_increments())


def table_digests() -> dict[str, str]:
    """SHA-256 of each table's little-endian bytes, as listed in the spec (D4)."""
    return {
        "sine_int32le": _digest(SINE, "<i4"),
        "attack_int32le": _digest(ATTACK, "<i4"),
        "release_int32le": _digest(RELEASE, "<i4"),
        "increment_uint32le": _digest(INCREMENTS.reshape(-1), "<u4"),
    }


def increment(profile: Profile, pitch: int, harmonic: int) -> int:
    """Phase increment per sample (Q32 of a cycle) for one partial."""
    return int(INCREMENTS[list(Profile).index(profile), pitch - PITCHES[0], harmonic - 1])


_EXPECTED = {
    "sine_int32le": SINE_SHA256,
    "attack_int32le": ATTACK_SHA256,
    "release_int32le": RELEASE_SHA256,
    "increment_uint32le": INCREMENT_SHA256,
}

if table_digests() != _EXPECTED:  # pragma: no cover - only on a broken platform
    raise RuntimeError(f"renderer tables do not match the spec digests: {table_digests()}")
