"""Exact-integer tables (renderer spec D2, D4)."""

from __future__ import annotations

import math

import numpy as np

from av_sound import Profile
from av_sound.recipe import PITCHES
from av_sound.tables import (
    ATTACK,
    ATTACK_SHA256,
    ENV_ONE,
    INCREMENT_SHA256,
    RELEASE,
    RELEASE_SHA256,
    SINE,
    SINE_Q,
    SINE_SHA256,
    SINE_SIZE,
    increment,
    table_digests,
)


def test_digests_match_spec():
    assert table_digests() == {
        "sine_int32le": SINE_SHA256,
        "attack_int32le": ATTACK_SHA256,
        "release_int32le": RELEASE_SHA256,
        "increment_uint32le": INCREMENT_SHA256,
    }


def test_tables_are_read_only():
    for table in (SINE, ATTACK, RELEASE):
        assert not table.flags.writeable


def test_sine_is_odd_symmetric_and_exact_at_quarter_points():
    q = SINE_SIZE // 4
    assert SINE[0] == 0 and SINE[2 * q] == 0
    assert SINE[q] == 1 << SINE_Q and SINE[3 * q] == -(1 << SINE_Q)
    i = np.arange(1, SINE_SIZE)
    assert np.array_equal(SINE[i], -SINE[SINE_SIZE - i])


def test_sine_agrees_with_float_sine_within_rounding():
    i = np.arange(SINE_SIZE)
    ref = np.sin(2 * np.pi * i / SINE_SIZE) * (1 << SINE_Q)
    assert np.max(np.abs(SINE - ref)) <= 0.5 + 1e-6


def test_envelope_tables():
    assert ATTACK[0] == 0 and ATTACK[-1] == ENV_ONE
    assert RELEASE[0] == 0 and RELEASE[-1] == ENV_ONE
    # cos(pi/3) = 1/2 and cos(2pi/3) = -1/2 give exact quarter values
    assert (
        ATTACK[160] == ENV_ONE // 4
        and ATTACK[240] == ENV_ONE // 2
        and ATTACK[320] == 3 * ENV_ONE // 4
    )
    k = np.arange(ATTACK.size)
    ref = (1 - np.cos(np.pi * k / 480)) / 2 * ENV_ONE
    assert np.max(np.abs(ATTACK - ref)) <= 0.5 + 1e-3


def test_increments_match_formula():
    for profile in Profile:
        for pitch in PITCHES:
            for h in (1, 2, 3):
                f = h * profile.f0_hz * 2 ** (pitch / 12)
                assert abs(increment(profile, pitch, h) - f * 2**32 / 48000) <= 0.5 + 1e-3


def test_highest_partial_is_far_below_nyquist():
    top = increment(Profile.P3, 6, 3) * 48000 / 2**32
    assert math.isclose(top, 3 * 675 * 2**0.5, rel_tol=1e-6)
    assert top < 3000 < 24000
