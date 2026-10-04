"""Deterministic sound engine for the acoustic-vocabulary studies.

Stable public API (see `sound/README.md` and `docs/interfaces/sound-engine.md`):

- `Recipe`, `Profile`, `RecipeError`: the recipe contract (`sound/schema/recipe.schema.json`).
- `render(recipe, profile) -> Rendered`: the only path that creates a study sound.
- `write_wav`, `wav_bytes`, `read_wav`, `pcm_sha256`, `file_sha256`: canonical WAV I/O and hashes.
- `RENDERER_VERSION`, `renderer_hash`, `renderer_recipe_schema_hash`: provenance.
"""

from av_sound.recipe import Profile, Recipe, RecipeError
from av_sound.renderer import (
    MIN_EVENT_SAMPLES,
    RENDERER_VERSION,
    RMS_TARGET,
    Rendered,
    Timing,
    event_samples,
    render,
    timing,
)
from av_sound.version import renderer_hash, renderer_manifest, renderer_recipe_schema_hash
from av_sound.wav import file_sha256, pcm_sha256, read_wav, wav_bytes, write_wav

__all__ = [
    "MIN_EVENT_SAMPLES",
    "RENDERER_VERSION",
    "RMS_TARGET",
    "Profile",
    "Recipe",
    "RecipeError",
    "Rendered",
    "Timing",
    "event_samples",
    "file_sha256",
    "pcm_sha256",
    "read_wav",
    "render",
    "renderer_hash",
    "renderer_manifest",
    "renderer_recipe_schema_hash",
    "timing",
    "wav_bytes",
    "write_wav",
]
