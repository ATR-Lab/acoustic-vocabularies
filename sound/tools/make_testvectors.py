"""Write the renderer reference vectors (synthetic recipes only).

Usage: uv run --project sound python sound/tools/make_testvectors.py
Rewrites sound/testvectors/renderer/vectors.json. A change in any hash means the
rendered bytes changed: bump RENDERER_VERSION in the same pull request.
"""

from __future__ import annotations

import json
from pathlib import Path

from av_sound import (
    RENDERER_VERSION,
    Profile,
    Recipe,
    file_sha256,
    render,
    renderer_hash,
    renderer_recipe_schema_hash,
)

OUT = Path(__file__).resolve().parents[1] / "testvectors" / "renderer" / "vectors.json"

# Synthetic corner cases; none of these is a study motif.
RECIPES: list[tuple[str, dict[str, object]]] = [
    (
        "spec-worked-example",
        {
            "total_ms": 600,
            "pitches": [-3, 0, 4],
            "rhythm_weights": [2, 1, 3],
            "gaps_ms": [40, 20],
            "amplitudes": [1.0, 0.6, 0.8],
        },
    ),
    (
        "shortest-total-low",
        {
            "total_ms": 450,
            "pitches": [-6, -6, -6],
            "rhythm_weights": [1, 1, 1],
            "gaps_ms": [20, 20],
            "amplitudes": [0.6, 0.6, 0.6],
        },
    ),
    (
        "longest-total-high",
        {
            "total_ms": 900,
            "pitches": [6, 6, 6],
            "rhythm_weights": [4, 4, 4],
            "gaps_ms": [60, 60],
            "amplitudes": [1.0, 1.0, 1.0],
        },
    ),
    (
        "shortest-legal-event",
        {
            "total_ms": 600,
            "pitches": [0, 6, -6],
            "rhythm_weights": [1, 4, 4],
            "gaps_ms": [20, 40],
            "amplitudes": [1.0, 0.8, 0.6],
        },
    ),
    (
        "worst-crest-factor",
        {
            "total_ms": 450,
            "pitches": [-2, -2, -2],
            "rhythm_weights": [1, 1, 3],
            "gaps_ms": [60, 60],
            "amplitudes": [0.6, 1.0, 0.6],
        },
    ),
    (
        "mixed-750",
        {
            "total_ms": 750,
            "pitches": [5, -4, 1],
            "rhythm_weights": [3, 1, 2],
            "gaps_ms": [40, 60],
            "amplitudes": [0.8, 1.0, 0.6],
        },
    ),
    (
        "short-event-flagged",
        {
            "total_ms": 450,
            "pitches": [0, 2, 4],
            "rhythm_weights": [1, 4, 4],
            "gaps_ms": [60, 60],
            "amplitudes": [1.0, 0.8, 0.6],
        },
    ),
]


def build() -> dict[str, object]:
    vectors = []
    for name, data in RECIPES:
        recipe = Recipe.from_dict(data)
        for profile in Profile:
            r = render(recipe, profile)
            vectors.append(
                {
                    "name": name,
                    "profile": profile.value,
                    "recipe": recipe.to_dict(),
                    "n_samples": r.n_samples,
                    "event_samples": list(r.event_samples),
                    "event_onsets": list(r.timing.event_onsets),
                    "short_event": r.short_event,
                    "overflow": r.overflow,
                    "peak": r.peak,
                    "pcm_sha256": r.pcm_sha256,
                    "file_sha256": file_sha256(r),
                }
            )
    return {
        "renderer_version": RENDERER_VERSION,
        "renderer_hash": renderer_hash(),
        "renderer_recipe_schema_hash": renderer_recipe_schema_hash(),
        "synthetic": True,
        "vectors": vectors,
    }


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(build(), indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
