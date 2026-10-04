# Sound engine interface (`av_sound`)

Producer: sound engine (#7–#15). Consumers: generation methods and orchestrator
(#16–#25), Study B bank builder (#26–#28), analysis (#33–#35).

The package is used from this repository as an editable or path dependency
(`av-sound @ {root}/sound`). Its data files (`sound/schema/`, `sound/reserved/`) are
read from the source tree. All functions are pure and deterministic: no network, no
clock and no global random state.

## Renderer (#8)

```python
render(recipe: Recipe | Mapping[str, Any], profile: Profile | str) -> Rendered
```

- `recipe` must be in domain. `Recipe(...)`, `Recipe.from_dict()` and
  `Recipe.from_json()` raise `RecipeError` (`.code` is `E_SCHEMA` or `E_DOMAIN`),
  or `json.JSONDecodeError` for invalid JSON.
- `Rendered.n_samples == recipe.total_ms * 48` always.
- `Rendered.pcm` is int16 LE mono bytes. It raises `OverflowError` when `overflow`
  is set.
- Flags: `overflow` (any sample beyond ±32,767 after normalization; never
  clipped), `short_event` (any event under 2,880 samples), `nonfinite` (always
  false: integer pipeline).
- `Rendered.renderer_version` is stored with every hash.

Hashes (lowercase hex SHA-256):

| Function | Covers |
| --- | --- |
| `pcm_sha256(audio)` | Sample bytes only; the waveform identity |
| `file_sha256(audio)` | The canonical WAV file (44-byte header + samples) |
| `renderer_hash()` | Renderer constants, table digests and source digests |
| `renderer_recipe_schema_hash()` | `renderer_hash` + recipe schema digest; apparatus manifest field |

WAV files are canonical (spec D8). `read_wav()` rejects any other layout.

## Validator (#9), composer (#10), store (#11), fallback (#15), packages (#13)

*Pending.* Each pull request adds its section here: `validate()`,
`nearest_reference()`, `compose_message()`, `composite_hash()`,
`message_length()`, the vocabulary store, `scan_fallback()` and the package
builder.
