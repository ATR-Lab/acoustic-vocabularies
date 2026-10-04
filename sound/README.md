# Sound engine

Python sound rendering tooling (`av-sound`, import `av_sound`). Public fixtures must
be synthetic and nonlexical; study sounds and vocabularies remain outside this
repository.

The engine turns a recipe and a profile into a deterministic 48 kHz mono WAV. Every
study sound is made this way, by all generation methods and the Study B bank
builder. The rules are in [`docs/renderer-spec.md`](docs/renderer-spec.md) (pilot
defaults; they freeze at G4).

## Setup

Python 3.11 and [uv](https://docs.astral.sh/uv/). From the repository root:

```bash
uv sync --project sound --locked
uv run --project sound pytest --import-mode=importlib tests/sound
```

Nothing needs the network at runtime.

## Layout

| Path | Contents |
| --- | --- |
| `src/av_sound/` | The package |
| `schema/recipe.schema.json` | Recipe contract (enum-only JSON Schema) |
| `schema/validation-result.schema.json`, `schema/reserved-registry.schema.json`, `schema/validator-config.schema.json` | Validator result, reserved registry and validator config formats |
| `config/validator.json` | Separation threshold (`"0.10"`, pilot default; freezes at G4) |
| `reserved/registry.json` | Reserved-signal registry: the seven nonlexical assets (#14, [`docs/nonlexical.md`](docs/nonlexical.md)) |
| `docs/` | Renderer spec and component docs |
| `testvectors/renderer/vectors.json` | Reference hashes for synthetic recipes |
| `testvectors/composition/vectors.json` | Atom and composite message hashes for three synthetic books |
| `testvectors/validator/boundary.json` | Separation-boundary fixtures (synthetic) |
| `tools/` | Spec evidence and generators: shortest events, headroom sweep, spectral check, test-vector writers, separation boundary, validator benchmark, reserved assets (`make_reserved_assets.py`) |

## API

Stable entry points, exported from `av_sound`. The full contract is in
[`docs/interfaces/sound-engine.md`](../docs/interfaces/sound-engine.md).

| Name | Purpose |
| --- | --- |
| `Recipe`, `Recipe.from_dict()`, `Recipe.from_json()`, `.canonical_json()`, `.sha256()` | Recipe value; raises `RecipeError` with code `E_JSON` (not strict JSON: invalid, duplicate keys, NaN), `E_SCHEMA` or `E_DOMAIN` |
| `Profile` (`P1`, `P2`, `P3`; `.f0_hz`) | Frozen render profiles: 300, 450 and 675 Hz |
| `render(recipe, profile) -> Rendered` | The only way to create a study sound |
| `Rendered` | `.samples`, `.pcm`, `.pcm_sha256`, `.timing`, `.event_samples`, `.n_samples`, `.peak`, `.rms`, `.overflow`, `.short_event`, `.nonfinite`, `.renderer_version` |
| `event_samples(total_ms, weights, gaps)`, `timing(recipe)` | Sample layout without rendering |
| `write_wav(audio, path)`, `wav_bytes(audio)`, `read_wav(path)` | Canonical WAV I/O (`audio` is a `Rendered` or PCM bytes) |
| `pcm_sha256(audio)`, `file_sha256(audio)` | Waveform hash and file hash |
| `RENDERER_VERSION`, `renderer_hash()`, `renderer_recipe_schema_hash()` | Provenance for store records and the apparatus manifest (pinned in `testvectors/renderer/vectors.json`) |
| `self_test()` | Renders two pinned reference vectors; call at start-up on a generation host |
| `SAMPLE_RATE`, `SAMPLES_PER_MS`, `MIN_EVENT_SAMPLES`, `RMS_TARGET` | Constants (48,000; 48; 2,880; 7,336) |
| `validate(candidate, profile, committed=(), *, reserved=None, threshold=None) -> ValidationResult` | Admissibility check; all failing reason codes in a fixed order ([`docs/validator.md`](docs/validator.md)) |
| `Reference(ref_id, recipe, pcm_sha256, profile)`, `Reference.from_rendered(ref_id, rendered)` | A committed motif or retained bank option to check against |
| `nearest_reference(candidate, committed) -> NearestReference \| None` | Closest committed reference by 12-feature distance; ties to the lowest index |
| `features(recipe)`, `sum_squared_diff(a, b)`, `distance(a, b)`, `separated(a, b, threshold)` | Exact 12-feature metric; `distance` is a float for reports |
| `REASON_CODES`, `VALIDATOR_VERSION`, `load_separation_threshold()`, `parse_threshold()` | Codes, version and the configured threshold (exact `Fraction`) |
| `load_reserved_registry()`, `ReservedRegistry`, `ReservedEntry` | Reserved signals (`E_RESERVED`) |
| `nonlexical_assets()`, `nonlexical_asset(id)`, `calibration_example(profile)` -> `NonlexicalAsset` | Calibration examples (96,000 samples per profile), READY cue and grammar clicks; `.pcm`, `.pcm_sha256`, `.file_sha256`, `.segments`, levels ([`docs/nonlexical.md`](docs/nonlexical.md)) |
| `build_reserved_registry()`, `CALIBRATION_SAMPLES` | The registry `reserved/registry.json` must equal; 96,000 |
| `AtomAudio(atom_id, profile, pcm)`, `AtomAudio.from_rendered()` | One committed atom (`K-a1` .. `Q-r4`); any object with `atom_id`, `profile`, `pcm` also works |
| `compose_message(action, referent, *, heldout=None, audit=None) -> Message` (alias `compose`) | Action + 9,600 zero samples + referent; refuses held-out IDs (`HeldOutMessageError`) and mixed profiles, families or roles (`CompositionError`) |
| `composite_hash(action, referent) -> str` | Expected SHA-256 of a message, held-out included; returns no samples |
| `message_length(action, referent) -> int` | Message samples from metadata (`total_ms`, recipe, atom); never renders |
| `write_message_wav(message, path) -> str` | Canonical WAV of a trained message; returns `file_sha256` |
| `GAP_SAMPLES`, `MIN_MESSAGE_SAMPLES`, `MAX_MESSAGE_SAMPLES` | 9,600; 52,800; 96,000 |
| `av_sound.grammar`, `av_sound.synthetic` | Atom and message IDs and the fixed matrix (18 trained, 14 held out); synthetic `DEMO-P1` .. `DEMO-P3` books |

```python
from av_sound import Profile, Recipe, render, write_wav

recipe = Recipe.from_json(
    '{"total_ms":600,"pitches":[-3,0,4],"rhythm_weights":[2,1,3],'
    '"gaps_ms":[40,20],"amplitudes":[1.0,0.6,0.8]}'
)
motif = render(recipe, Profile.P2)
assert motif.n_samples == 600 * 48
file_hash = write_wav(motif, "example.wav")  # never commit WAVs from study recipes
```

Messages follow the byte contract in [`docs/composition.md`](docs/composition.md).

The renderer does not decide admissibility. It reports `overflow` and
`short_event` flags and never limits or repairs a recipe. `validate()` decides:

```python
from av_sound import Reference, validate

committed = [Reference.from_rendered("K-a1", motif)]
result = validate(
    '{"total_ms":450,"pitches":[0,0,0],"rhythm_weights":[1,4,4],'
    '"gaps_ms":[60,60],"amplitudes":[1.0,0.8,0.6]}',
    Profile.P2,
    committed,
)
assert result.codes == ("E_EVENT_SHORT",)  # first event 1,760 samples (36.7 ms)
```

## Versioning

`RENDERER_VERSION` is `0.1.0` until the G4 freeze, when it becomes `1.0.0`. Any
change to rendered bytes bumps it in the same pull request and regenerates
`testvectors/` with `uv run --project sound python sound/tools/make_testvectors.py` and
`uv run --project sound python sound/tools/make_composition_vectors.py`, and rewrites the
reserved registry with `uv run --project sound python sound/tools/make_reserved_assets.py`.
A code change that leaves the bytes unchanged still changes `renderer_hash`: regenerate
the vectors to update the pins and say why in the pull request.
CI renders the vectors on Linux, macOS and Windows and fails on any hash change.
