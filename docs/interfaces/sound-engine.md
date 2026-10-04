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
  `Recipe.from_json()` raise `RecipeError` with `.code` `E_JSON` (invalid JSON,
  duplicate keys, `NaN`/`Infinity`), `E_SCHEMA` or `E_DOMAIN`. The constants are
  exported from `av_sound`.
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

Both provenance hashes are pinned in `sound/testvectors/renderer/vectors.json`.
`self_test()` renders two pinned vectors and raises `RuntimeError` on any
difference; generation hosts should call it at start-up.

WAV files are canonical (spec D8). `read_wav()` rejects any other layout.

## Validator (#9)

```python
validate(
    candidate: Recipe | Mapping[str, Any] | str | bytes | bytearray,
    profile: Profile | str,
    committed: Iterable[Reference] = (),
    *,
    reserved: ReservedRegistry | Iterable[ReservedEntry] | None = None,
    threshold: Fraction | Decimal | int | str | None = None,
) -> ValidationResult
nearest_reference(candidate, committed, *, profile=None) -> NearestReference | None
Reference(ref_id: str, recipe: Recipe, pcm_sha256: str, profile: Profile)
Reference.from_rendered(ref_id, rendered) -> Reference
```

- `candidate` is raw JSON text or UTF-8 bytes, a decoded JSON object or a `Recipe`.
  A bad candidate never raises; it gives a result with reason codes.
- `committed`: the references of one book and one profile (both families and roles),
  in commit order; the position is the atom index. A reference with another profile
  raises `ValueError`. Study B passes the retained options of the other atoms.
- `reserved=None` loads `sound/reserved/registry.json`; `threshold=None` loads
  `sound/config/validator.json` (`"0.10"`, pilot default; freezes at G4).
- `ValidationResult`: `ok`, `codes`, `messages` (one per code), `primary_code`,
  `profile`, `threshold` (`Fraction`), `recipe` (canonical, if parsed), `features`,
  `event_samples`, `pcm_sha256` (`None` if not parsed, `E_NONFINITE` or `E_CLIP`),
  `nearest_id`, `nearest_index`, `nearest_distance`, `validator_version`,
  `renderer_version`, `rendered` (not serialized). `to_dict()` matches
  `sound/schema/validation-result.schema.json`.
- Reason codes (`REASON_CODES`, fixed order): `E_JSON`, `E_SCHEMA`, `E_DOMAIN`,
  `E_EVENT_SHORT`, `E_NONFINITE`, `E_CLIP`, `E_DUPLICATE`, `E_RESERVED`,
  `E_SEPARATION`. If parsing or the schema fails, only those codes are listed;
  otherwise every remaining check runs on one render.
- `NearestReference`: `ref_id`, `index`, `distance`, `sum_sq` (exact). Ties go to the
  lowest index; `None` when nothing is committed.

Feature metric (Study A protocol §3.2), for the listening tool (#23) and reports:

| Function | Returns |
| --- | --- |
| `features(recipe)` | 12 exact `Fraction`s in [0, 1] (`FEATURE_NAMES` order) |
| `sum_squared_diff(a, b)` | Exact `sum((x_j - y_j)^2)`; `a`, `b` are recipes or 12-value vectors |
| `distance(a, b)` | `sqrt(sum / 12)` as a float, for reporting only |
| `separated(a, b, threshold)` | `sum >= 12 * threshold^2`, decided exactly |
| `parse_threshold(value)`, `load_separation_threshold(path=None)` | Exact threshold (`"0.10"` -> 1/10) |

Reserved signals: `load_reserved_registry(path=None) -> ReservedRegistry`
(`registry_version`, `renderer_version`, `entries`), `ReservedEntry` (`id`, `kind`,
`profile`, `n_samples`, `pcm_sha256`, `file_sha256`, `recipe`, `description`).
Format: `sound/schema/reserved-registry.schema.json`. Details:
[`sound/docs/validator.md`](../../sound/docs/validator.md).

Note: the package attribute `av_sound.validate` is the function. Import names from
the module with `from av_sound.validate import ...`.

## Composer (#10)

Byte contract for other implementations (Unity, #64):
[`sound/docs/composition.md`](../../sound/docs/composition.md). Message =
`pcm(action)` + 9,600 zero samples + `pcm(referent)`; 52,800 to 96,000 samples.

```python
class AtomAudioLike(Protocol):            # e.g. a store entry (#11)
    atom_id: str                          # "K-a1" .. "Q-r4" (read-only properties)
    profile: Profile | str
    pcm: bytes                            # int16 LE mono

AtomAudio(atom_id: str, profile: Profile | str, pcm: bytes)
AtomAudio.from_rendered(atom_id: str, rendered: Rendered) -> AtomAudio

compose_message(action: AtomAudioLike, referent: AtomAudioLike, *,
                heldout: Iterable[str] | None = None,
                audit: Callable[[Mapping[str, str]], None] | None = None) -> Message
compose = compose_message
composite_hash(action: AtomAudioLike, referent: AtomAudioLike) -> str
message_length(action: MotifMetadata, referent: MotifMetadata) -> int
write_message_wav(message: Message, path: str | os.PathLike[str], *,
                  heldout: Iterable[str] | None = None,
                  audit: Callable[[Mapping[str, str]], None] | None = None) -> str
```

- `Message`: `message_id`, `profile`, `action_id`, `referent_id`,
  `action_samples`, `referent_samples`, `action_pcm_sha256`,
  `referent_pcm_sha256`, `pcm`, `pcm_sha256`, `n_samples`, `.duration_s`,
  `.referent_onset`.
- `compose_message` checks, in order: action then referent (`E_ROLE_ORDER`), one
  family (`E_FAMILY_MISMATCH`), not held out (`HeldOutMessageError`, code
  `E_HELDOUT`), one profile (`E_PROFILE_MISMATCH`), motif length 21,600, 28,800,
  36,000 or 43,200 samples (`E_MOTIF_LENGTH`). All are `CompositionError`
  (a `ValueError`) with `.code`.
- Held-out guard: `heldout` is the set of held-out message IDs (curriculum status
  table, #29). The default is the 14 held-out IDs of the fixed matrix
  (`av_sound.grammar.HELDOUT_MESSAGE_IDS`). A refusal reads no samples, writes
  nothing, logs a warning on logger `av_sound.composer` and calls `audit` with
  `{event, operation, message_id, action_id, referent_id}`.
- `composite_hash` has the same structural checks but is allowed for held-out
  messages. It hashes incrementally and returns only the lowercase hex digest
  (the hidden-answer manifest value, #13).
- `message_length` uses metadata only and never renders: each argument is a
  `total_ms` int, a `Recipe` or recipe dict, or an object with `n_samples`,
  `recipe` or `pcm` (checked in that order).
- `write_message_wav` writes the canonical WAV of a trained message and returns
  its `file_sha256`. It refuses held-out IDs and a `Message` whose samples do
  not match its hash (`E_INTEGRITY`).
- Constants: `GAP_SAMPLES = 9600`, `MIN_MESSAGE_SAMPLES = 52800`,
  `MAX_MESSAGE_SAMPLES = 96000`.

Grammar (`av_sound.grammar`): `FAMILIES`, `ROLES`, `INDICES`, `MATRIX`,
`HELDOUT_SETS`, `ATOM_IDS` (16), `MESSAGES` (32 `MessageRef`), `TRAINED_MESSAGE_IDS`
(18), `HELDOUT_MESSAGE_IDS` (14), `atom_id()`, `message_id()`, `parse_atom_id()`,
`parse_message_id()` (raise `GrammarError`). `MessageRef` has `.message_id`,
`.action`, `.referent`, `.status`, `.training_wave`, `.heldout_set`,
`.is_heldout`.

Synthetic fixtures (`av_sound.synthetic`, not study material):
`synthetic_recipes(profile) -> dict[str, Recipe]`,
`synthetic_book(profile) -> dict[str, AtomAudio]`, `synthetic_book_id(profile)`
(`DEMO-P1` .. `DEMO-P3`).

## Store (#11), fallback (#15), packages (#13)

*Pending.* Each pull request adds its section here: the vocabulary store,
`scan_fallback()` and the package builder.
