"""Deterministic sound engine for the acoustic-vocabulary studies.

Stable public API (see `sound/README.md` and `docs/interfaces/sound-engine.md`):

- `Recipe`, `Profile`, `RecipeError` (`E_JSON`, `E_SCHEMA`, `E_DOMAIN`): the recipe
  contract (`sound/schema/recipe.schema.json`).
- `render(recipe, profile) -> Rendered`: the only path that creates a study sound.
- `write_wav`, `wav_bytes`, `read_wav`, `pcm_sha256`, `file_sha256`: canonical WAV I/O and hashes.
- `RENDERER_VERSION`, `renderer_hash`, `renderer_recipe_schema_hash`, `self_test`: provenance.
- `compose_message`, `composite_hash`, `message_length`, `write_message_wav`: messages
  (action + 9,600 zero samples + referent; `sound/docs/composition.md`).
- `validate(candidate, profile, committed) -> ValidationResult`, `nearest_reference`,
  `Reference`: admissibility checks and the 12-feature separation screen.
- `features`, `distance`, `separated`, `sum_squared_diff`: the exact feature metric.
- `load_reserved_registry`, `ReservedEntry`, `ReservedRegistry`: reserved signals.
- `nonlexical_assets`, `nonlexical_asset`, `calibration_example`, `NonlexicalAsset`,
  `build_reserved_registry`, `CALIBRATION_SAMPLES`: calibration examples, READY cue and
  grammar clicks (`sound/docs/nonlexical.md`).
- `VocabularyStore`, `StoreEntry`, `VerifyReport`: the append-only vocabulary store
  (`sound/docs/store.md`).
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
from av_sound.features import (
    FEATURE_NAMES,
    distance,
    features,
    parse_threshold,
    separated,
    sum_squared_diff,
)
from av_sound.grammar import GrammarError
from av_sound.nonlexical import (
    CALIBRATION_SAMPLES,
    NonlexicalAsset,
    build_reserved_registry,
    calibration_example,
    nonlexical_asset,
    nonlexical_assets,
)
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
from av_sound.reserved import ReservedEntry, ReservedRegistry, load_reserved_registry
from av_sound.selftest import self_test
from av_sound.store import (
    BookFrozen,
    BookInfo,
    CommitRejected,
    OverwriteRejected,
    StoreEntry,
    StoreError,
    StoreIntegrityError,
    StoreLocked,
    VerifyIssue,
    VerifyReport,
    VocabularyStore,
    persistence_violations,
    snapshot_digest,
)
from av_sound.tables import SAMPLE_RATE, SAMPLES_PER_MS
from av_sound.validate import (
    REASON_CODES,
    VALIDATOR_VERSION,
    NearestReference,
    Reference,
    ValidationResult,
    load_separation_threshold,
    nearest_reference,
    validate,
)
from av_sound.version import renderer_hash, renderer_manifest, renderer_recipe_schema_hash
from av_sound.wav import file_sha256, pcm_sha256, read_wav, wav_bytes, write_wav

__all__ = [
    "CALIBRATION_SAMPLES",
    "E_DOMAIN",
    "E_JSON",
    "E_SCHEMA",
    "FEATURE_NAMES",
    "GAP_SAMPLES",
    "MAX_MESSAGE_SAMPLES",
    "MIN_EVENT_SAMPLES",
    "MIN_MESSAGE_SAMPLES",
    "REASON_CODES",
    "RENDERER_VERSION",
    "RMS_TARGET",
    "SAMPLES_PER_MS",
    "SAMPLE_RATE",
    "VALIDATOR_VERSION",
    "AtomAudio",
    "AtomAudioLike",
    "BookFrozen",
    "BookInfo",
    "CommitRejected",
    "CompositionError",
    "GrammarError",
    "HeldOutMessageError",
    "Message",
    "NearestReference",
    "NonlexicalAsset",
    "OverwriteRejected",
    "Profile",
    "Recipe",
    "RecipeError",
    "Reference",
    "Rendered",
    "ReservedEntry",
    "ReservedRegistry",
    "StoreEntry",
    "StoreError",
    "StoreIntegrityError",
    "StoreLocked",
    "Timing",
    "ValidationResult",
    "VerifyIssue",
    "VerifyReport",
    "VocabularyStore",
    "build_reserved_registry",
    "calibration_example",
    "compose",
    "compose_message",
    "composite_hash",
    "distance",
    "event_samples",
    "features",
    "file_sha256",
    "load_reserved_registry",
    "load_separation_threshold",
    "message_length",
    "nearest_reference",
    "nonlexical_asset",
    "nonlexical_assets",
    "parse_threshold",
    "pcm_sha256",
    "persistence_violations",
    "read_wav",
    "render",
    "renderer_hash",
    "renderer_manifest",
    "renderer_recipe_schema_hash",
    "self_test",
    "separated",
    "snapshot_digest",
    "sum_squared_diff",
    "timing",
    "validate",
    "wav_bytes",
    "write_message_wav",
    "write_wav",
]
