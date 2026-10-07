"""Deterministic motif renderer (renderer spec D1-D7).

`render(recipe, profile)` is the only path by which a study sound is created. It
uses integer arithmetic only. It reports overflow and short events as flags and
never limits or repairs; admissibility is the validator's decision.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from math import gcd, isqrt
from typing import Any

import numpy as np

from av_sound.recipe import Profile, Recipe
from av_sound.tables import (
    ATTACK,
    ATTACK_SAMPLES,
    HARMONICS,
    PHASE_BITS,
    RELEASE,
    RELEASE_SAMPLES,
    SAMPLES_PER_MS,
    SINE,
    SINE_BITS,
    IntArray,
    increment,
)

RENDERER_VERSION = "0.1.0"
"""Bumped whenever rendered bytes change for any valid recipe (spec D10)."""

RMS_TARGET = 7336
"""Output RMS in LSB, -13.00 dB re full-scale code 32,767 (spec D6)."""

FULL_SCALE = 32767
MIN_EVENT_SAMPLES = 60 * SAMPLES_PER_MS
HARMONIC_WEIGHTS_X20: tuple[int, ...] = (20, 3, 1)
WORK_SHIFT = 40
GAIN_FRAC_BITS = 32
_PHASE_MASK = (1 << PHASE_BITS) - 1
_INDEX_SHIFT = PHASE_BITS - SINE_BITS


@dataclass(frozen=True, slots=True)
class Timing:
    """Sample layout of a motif (spec D1)."""

    event_samples: tuple[int, int, int]
    event_onsets: tuple[int, int, int]
    gap_samples: tuple[int, int]
    n_samples: int


def event_samples(
    total_ms: int, rhythm_weights: tuple[int, int, int], gaps_ms: tuple[int, int]
) -> tuple[int, int, int]:
    """Events 1 and 2 rounded half up, event 3 takes the remainder (spec D1)."""
    d = (total_ms - gaps_ms[0] - gaps_ms[1]) * SAMPLES_PER_MS
    w = sum(rhythm_weights)
    n1 = (2 * d * rhythm_weights[0] + w) // (2 * w)
    n2 = (2 * d * rhythm_weights[1] + w) // (2 * w)
    return n1, n2, d - n1 - n2


def timing(recipe: Recipe) -> Timing:
    """Event lengths, onsets and gap lengths, all in samples."""
    n1, n2, n3 = event_samples(recipe.total_ms, recipe.rhythm_weights, recipe.gaps_ms)
    g1, g2 = (g * SAMPLES_PER_MS for g in recipe.gaps_ms)
    onsets = (0, n1 + g1, n1 + g1 + n2 + g2)
    return Timing((n1, n2, n3), onsets, (g1, g2), recipe.total_ms * SAMPLES_PER_MS)


AMPLITUDE_STEPS: dict[float, int] = {0.6: 3, 0.8: 4, 1.0: 5}
"""Lookup of k = 5a (spec D5); no float arithmetic enters the sample path."""


def amplitude_steps(amplitudes: tuple[float, float, float]) -> tuple[int, int, int]:
    """k = 5a in {3, 4, 5}, divided by gcd(k1, k2, k3) (spec D5)."""
    k = tuple(AMPLITUDE_STEPS[a] for a in amplitudes)
    common = gcd(gcd(k[0], k[1]), k[2])
    return k[0] // common, k[1] // common, k[2] // common


def envelope(n_samples: int) -> IntArray:
    """Per-event raised-cosine envelope in Q30 (spec D2): min(attack, release)."""
    i = np.arange(n_samples, dtype=np.int64)
    attack = ATTACK[np.minimum(i, ATTACK_SAMPLES)]
    release = RELEASE[np.minimum(n_samples - 1 - i, RELEASE_SAMPLES)]
    return np.minimum(attack, release)


def synth_event(profile: Profile, pitch: int, n_samples: int, amplitude_step: int) -> IntArray:
    """One enveloped event at working scale (spec D3, D4); partials start at phase 0."""
    i = np.arange(n_samples, dtype=np.int64)
    mix = np.zeros(n_samples, dtype=np.int64)
    for h, weight in zip(HARMONICS, HARMONIC_WEIGHTS_X20, strict=True):
        phase = (i * increment(profile, pitch, h)) & _PHASE_MASK
        mix += weight * SINE[phase >> _INDEX_SHIFT]
    e = mix * amplitude_step * envelope(n_samples)
    return (e + (1 << (WORK_SHIFT - 1))) >> WORK_SHIFT


def overflows(samples: IntArray) -> bool:
    """True if any sample is outside +/-32,767 (-32,768 counts as overflow; spec D7)."""
    return bool(np.any(np.abs(samples) > FULL_SCALE))


@dataclass(frozen=True, slots=True)
class Normalized:
    """Result of RMS normalization (spec D6)."""

    samples: IntArray
    gain_q32: int
    sum_squares_in: int
    overflow: bool


def normalize(x: IntArray, target_rms: int = RMS_TARGET) -> Normalized:
    """Scale to `target_rms` over the full length with an integer Q32 gain. Never limits."""
    sum_sq = int(np.sum(x * x))
    if sum_sq <= 0:
        raise ValueError("cannot normalize an all-zero signal")
    gain = isqrt((target_rms * target_rms * int(x.size) << (2 * GAIN_FRAC_BITS)) // sum_sq)
    y = (x * gain + (1 << (GAIN_FRAC_BITS - 1))) >> GAIN_FRAC_BITS
    overflow = overflows(y)
    y.setflags(write=False)
    return Normalized(y, gain, sum_sq, overflow)


@dataclass(frozen=True, slots=True, eq=False)
class Rendered:
    """Rendered motif: samples, metadata and the flags the validator inspects."""

    recipe: Recipe
    profile: Profile
    samples: IntArray = field(repr=False)
    timing: Timing
    peak: int
    sum_squares: int
    gain_q32: int
    overflow: bool
    nonfinite: bool
    short_event: bool
    renderer_version: str = RENDERER_VERSION

    @property
    def n_samples(self) -> int:
        """Always `total_ms * 48`."""
        return self.timing.n_samples

    @property
    def event_samples(self) -> tuple[int, int, int]:
        """Per-event sample counts (spec D1)."""
        return self.timing.event_samples

    @property
    def rms(self) -> float:
        """RMS in LSB, for reporting only (never used to make bytes)."""
        return math.sqrt(self.sum_squares / self.n_samples)

    @property
    def peak_dbfs(self) -> float:
        """Peak relative to full-scale code 32,767, for reporting only."""
        return 20 * math.log10(self.peak / FULL_SCALE) if self.peak else -math.inf

    @property
    def pcm(self) -> bytes:
        """int16 little-endian sample bytes. Raises `OverflowError` if the motif overflowed."""
        if self.overflow:
            raise OverflowError("motif exceeds full scale after normalization (E_CLIP)")
        return self.samples.astype("<i2").tobytes()

    @property
    def pcm_sha256(self) -> str:
        """SHA-256 of `pcm`, lowercase hex (spec D9)."""
        return hashlib.sha256(self.pcm).hexdigest()


def render(recipe: Recipe | Mapping[str, Any], profile: Profile | str) -> Rendered:
    """Render one recipe for one profile. Pure function: same input, same bytes."""
    if not isinstance(recipe, Recipe):
        recipe = Recipe.from_dict(recipe)
    profile = Profile(profile)
    layout = timing(recipe)
    steps = amplitude_steps(recipe.amplitudes)
    parts: list[IntArray] = []
    for j in range(3):
        parts.append(synth_event(profile, recipe.pitches[j], layout.event_samples[j], steps[j]))
        if j < 2:
            parts.append(np.zeros(layout.gap_samples[j], dtype=np.int64))
    x = np.concatenate(parts)
    out = normalize(x, RMS_TARGET)
    return Rendered(
        recipe=recipe,
        profile=profile,
        samples=out.samples,
        timing=layout,
        peak=int(np.max(np.abs(out.samples))),
        sum_squares=int(np.sum(out.samples * out.samples)),
        gain_q32=out.gain_q32,
        overflow=out.overflow,
        nonfinite=False,
        short_event=min(layout.event_samples) < MIN_EVENT_SAMPLES,
    )
