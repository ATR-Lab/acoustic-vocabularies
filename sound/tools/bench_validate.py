"""Benchmark `validate()` of one candidate against 15 committed motifs (issue #9).

Builds 15 synthetic admissible references from a fixed DEMO seed, then times the
validation of one JSON-text candidate (T = 900 ms, the longest render) and reports
the median, p95, minimum and maximum. The target is under 100 ms per candidate on
the generation host (one proposal slot is 40 s).

Usage: uv run --project sound python sound/tools/bench_validate.py [runs]
"""

from __future__ import annotations

import hashlib
import platform
import random
import statistics
import sys
import time

from av_sound import Profile, Recipe, Reference, render, validate
from av_sound.recipe import AMPLITUDES, GAPS_MS, PITCHES, RHYTHM_WEIGHTS, TOTAL_MS

SEED = "DEMO-bench-validate"
PROFILE = Profile.P1
N_REFERENCES = 15
CANDIDATE = (
    '{"total_ms":900,"pitches":[3,-2,5],"rhythm_weights":[2,3,1],'
    '"gaps_ms":[40,60],"amplitudes":[0.8,1.0,0.6]}'
)


def demo_references(n: int = N_REFERENCES) -> list[Reference]:
    """`n` admissible, distinct synthetic references (not study motifs)."""
    rng = random.Random(int.from_bytes(hashlib.sha256(SEED.encode()).digest()[:8], "big"))
    refs: list[Reference] = []
    seen: set[str] = set()
    while len(refs) < n:
        recipe = Recipe(
            rng.choice(TOTAL_MS),
            tuple(rng.choice(PITCHES) for _ in range(3)),
            tuple(rng.choice(RHYTHM_WEIGHTS) for _ in range(3)),
            tuple(rng.choice(GAPS_MS) for _ in range(2)),
            tuple(rng.choice(AMPLITUDES) for _ in range(3)),
        )
        rendered = render(recipe, PROFILE)
        if rendered.short_event or rendered.pcm_sha256 in seen:
            continue
        seen.add(rendered.pcm_sha256)
        refs.append(Reference.from_rendered(f"DEMO-{len(refs) + 1}", rendered))
    return refs


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def main(runs: int) -> None:
    refs = demo_references()
    for _ in range(20):  # warm-up: schema, config and registry caches
        validate(CANDIDATE, PROFILE, refs)
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        result = validate(CANDIDATE, PROFILE, refs)
        samples.append((time.perf_counter() - t0) * 1000)
    render_ms = []
    recipe = Recipe.from_json(CANDIDATE)
    for _ in range(runs):
        t0 = time.perf_counter()
        render(recipe, PROFILE).pcm_sha256  # noqa: B018
        render_ms.append((time.perf_counter() - t0) * 1000)
    print(f"host: {platform.platform()} ({platform.machine()}), Python {sys.version.split()[0]}")
    label = f"validate(JSON text, {PROFILE.value}, {len(refs)} committed) x {runs}"
    print(f"{label}: codes {result.codes}")
    print(
        f"  median {statistics.median(samples):.3f} ms, p95 {percentile(samples, 0.95):.3f} ms, "
        f"min {min(samples):.3f} ms, max {max(samples):.3f} ms"
    )
    print(f"  of which render + hash: median {statistics.median(render_ms):.3f} ms")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 500)
