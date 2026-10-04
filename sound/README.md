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
| `docs/` | Renderer spec and component docs |
| `testvectors/renderer/vectors.json` | Reference hashes for synthetic recipes |
| `tools/` | Spec evidence: shortest events, headroom sweep, spectral check, test-vector writer |

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

The renderer does not decide admissibility. It reports `overflow` and
`short_event` flags and never limits or repairs a recipe.

## Versioning

`RENDERER_VERSION` is `0.1.0` until the G4 freeze, when it becomes `1.0.0`. Any
change to rendered bytes bumps it in the same pull request and regenerates
`testvectors/` with `uv run --project sound python sound/tools/make_testvectors.py`.
A code change that leaves the bytes unchanged still changes `renderer_hash`: regenerate
the vectors to update the pins and say why in the pull request.
CI renders the vectors on Linux, macOS and Windows and fails on any hash change.
