"""Deterministic sound engine for the acoustic-vocabulary studies.

Stable public API (see `sound/README.md` and `docs/interfaces/sound-engine.md`):

- `Recipe`, `Profile`, `RecipeError` (`E_JSON`, `E_SCHEMA`, `E_DOMAIN`): the recipe
  contract (`sound/schema/recipe.schema.json`).
- `render(recipe, profile) -> Rendered`: the only path that creates a study sound.
- `write_wav`, `wav_bytes`, `read_wav`, `pcm_sha256`, `file_sha256`: canonical WAV I/O and hashes.
- `RENDERER_VERSION`, `renderer_hash`, `renderer_recipe_schema_hash`, `self_test`: provenance.
- `compose_message`, `composite_hash`, `message_length`, `write_message_wav`: messages
  (action + 9,600 zero samples + referent; `sound/docs/composition.md`).
"""

from av_sound.composer import (
    GAP_SAMPLES,
    MAX_MESSAGE_SAMPLES,
    MIN_MESSAGE_SAMPLES,
    AtomAudio,
    AtomAudioLike,
    CompositionError,
    HeldOutMessageError,
    Message,
    compose,
    compose_message,
    composite_hash,
    message_length,
    write_message_wav,
)
from av_sound.grammar import GrammarError
from av_sound.recipe import E_DOMAIN, E_JSON, E_SCHEMA, Profile, Recipe, RecipeError
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
from av_sound.selftest import self_test
from av_sound.tables import SAMPLE_RATE, SAMPLES_PER_MS
from av_sound.version import renderer_hash, renderer_manifest, renderer_recipe_schema_hash
from av_sound.wav import file_sha256, pcm_sha256, read_wav, wav_bytes, write_wav

__all__ = [
    "E_DOMAIN",
    "E_JSON",
    "E_SCHEMA",
    "GAP_SAMPLES",
    "MAX_MESSAGE_SAMPLES",
    "MIN_EVENT_SAMPLES",
    "MIN_MESSAGE_SAMPLES",
    "RENDERER_VERSION",
    "RMS_TARGET",
    "SAMPLES_PER_MS",
    "SAMPLE_RATE",
    "AtomAudio",
    "AtomAudioLike",
    "CompositionError",
    "GrammarError",
    "HeldOutMessageError",
    "Message",
    "Profile",
    "Recipe",
    "RecipeError",
    "Rendered",
    "Timing",
    "compose",
    "compose_message",
    "composite_hash",
    "event_samples",
    "file_sha256",
    "message_length",
    "pcm_sha256",
    "read_wav",
    "render",
    "renderer_hash",
    "renderer_manifest",
    "renderer_recipe_schema_hash",
    "self_test",
    "timing",
    "wav_bytes",
    "write_message_wav",
    "write_wav",
]
