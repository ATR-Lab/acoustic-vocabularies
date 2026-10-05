"""Reproduce the worst-case headroom sweep of renderer spec D6.

Stage 1: every admissible timing structure x every amplitude triple, pitch (0,0,0), P1.
Stage 2: the worst structures from stage 1 x all 2,197 pitch triples x all profiles.
Prints the worst crest factor and the resulting peak level at RMS_TARGET.

Usage: uv run --project sound python sound/tools/headroom_sweep.py [--top 15]
"""

from __future__ import annotations

import argparse
import itertools
import math

from av_sound import MIN_EVENT_SAMPLES, RMS_TARGET, Profile, Recipe, event_samples, render
from av_sound.recipe import AMPLITUDES, GAPS_MS, PITCHES, RHYTHM_WEIGHTS, TOTAL_MS
from av_sound.renderer import FULL_SCALE


def crest(recipe: Recipe, profile: Profile) -> float:
    r = render(recipe, profile)
    return r.peak / r.rms


def admissible_structures() -> list[tuple[int, tuple[int, ...], tuple[int, ...]]]:
    out = []
    for t in TOTAL_MS:
        for w in itertools.product(RHYTHM_WEIGHTS, repeat=3):
            for g in itertools.product(GAPS_MS, repeat=2):
                if min(event_samples(t, w, g)) >= MIN_EVENT_SAMPLES:  # type: ignore[arg-type]
                    out.append((t, w, g))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args()
    stage1 = []
    for t, w, g in admissible_structures():
        for a in itertools.product(AMPLITUDES, repeat=3):
            recipe = Recipe(t, (0, 0, 0), w, g, a)  # type: ignore[arg-type]
            stage1.append((crest(recipe, Profile.P1), recipe))
    stage1.sort(key=lambda item: -item[0])
    print(f"stage 1: {len(stage1)} recipes, worst crest {20 * math.log10(stage1[0][0]):.3f} dB")
    worst = (0.0, stage1[0][1], Profile.P1)
    for _, base in stage1[: args.top]:
        for profile in Profile:
            for p in itertools.product(PITCHES, repeat=3):
                recipe = Recipe(
                    base.total_ms, p, base.rhythm_weights, base.gaps_ms, base.amplitudes
                )  # type: ignore[arg-type]
                c = crest(recipe, profile)
                if c > worst[0]:
                    worst = (c, recipe, profile)
    c, recipe, profile = worst
    peak_dbfs = 20 * math.log10(RMS_TARGET * c / FULL_SCALE)
    print(
        f"stage 2: worst crest {c:.5f} = {20 * math.log10(c):.3f} dB, "
        f"{profile.value} {recipe.canonical_json()}"
    )
    print(
        f"RMS_TARGET {RMS_TARGET} = {20 * math.log10(RMS_TARGET / FULL_SCALE):.3f} dBFS -> "
        f"worst peak {peak_dbfs:.3f} dBFS ({-peak_dbfs:.2f} dB headroom)"
    )


if __name__ == "__main__":
    main()
