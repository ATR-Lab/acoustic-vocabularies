"""Start-up self-test: render pinned reference vectors and compare their hashes.

Table digests are checked at import. This also exercises the numpy arithmetic path,
so a generation host can confirm at start-up that it reproduces the reference bytes.
"""

from __future__ import annotations

from av_sound.recipe import Profile, Recipe
from av_sound.renderer import render

_PINNED: tuple[tuple[Recipe, Profile, str], ...] = (
    (
        Recipe(600, (-3, 0, 4), (2, 1, 3), (40, 20), (1.0, 0.6, 0.8)),
        Profile.P2,
        "4c0467de354c076c0b30bc9af794e31384afdf161af24605fb621cc455c35d87",
    ),
    (
        Recipe(450, (-2, -2, -2), (1, 1, 3), (60, 60), (0.6, 1.0, 0.6)),
        Profile.P1,
        "eb25c55778cc8f6f270fab74e2e44ec3d2350f46adf33bf47ac92fe4a3a4aea5",
    ),
)


def self_test() -> None:
    """Raise `RuntimeError` unless the pinned reference vectors render byte-identically."""
    for recipe, profile, expected in _PINNED:
        got = render(recipe, profile).pcm_sha256
        if got != expected:
            raise RuntimeError(
                f"renderer self-test failed for {profile.value} {recipe.canonical_json()}: "
                f"{got} != {expected}"
            )
