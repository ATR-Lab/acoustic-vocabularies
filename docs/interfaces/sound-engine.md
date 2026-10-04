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
(`registry_version`, `renderer_version`, `entries`, optional `asset_spec_version`), `ReservedEntry` (`id`, `kind`,
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

AtomAudio(atom_id: str, profile: Profile | str, pcm: bytes, *, book_id: str | None = None)
AtomAudio.from_rendered(atom_id: str, rendered: Rendered, *,
                        book_id: str | None = None) -> AtomAudio

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
  `referent_pcm_sha256`, `pcm`, `pcm_sha256`, `n_samples`, `book_id`
  (`None` unless an atom carries one), `.duration_s`, `.referent_onset`.
- `compose_message` checks, in order: action then referent (`E_ROLE_ORDER`), one
  family (`E_FAMILY_MISMATCH`), not held out (`HeldOutMessageError`, code
  `E_HELDOUT`), one profile (`E_PROFILE_MISMATCH`), one book when both atoms
  expose `book_id` (`E_BOOK_MISMATCH`), motif length 21,600, 28,800, 36,000 or
  43,200 samples (`E_MOTIF_LENGTH`). All are `CompositionError` (a `ValueError`)
  with `.code`.
- Held-out guard: the 14 held-out IDs of the fixed matrix
  (`av_sound.grammar.HELDOUT_MESSAGE_IDS`) are always refused. `heldout` adds
  message IDs to that set and can never remove one (`heldout=()` still refuses
  all 14). A refusal reads no samples, writes nothing, logs a warning on logger
  `av_sound.composer` and calls `audit` with
  `{event, operation, message_id, action_id, referent_id}`.
- `composite_hash` has the same role, family, profile, book and length checks,
  but it is allowed for held-out messages. It hashes incrementally and returns
  only the lowercase hex digest (the hidden-answer manifest value, #13).
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

## Nonlexical assets (#14)

Calibration examples, READY cue and grammar clicks: the only non-motif sounds that
participants hear. Byte recipes, levels and protocol sources:
[`sound/docs/nonlexical.md`](../../sound/docs/nonlexical.md).

```python
nonlexical_assets() -> tuple[NonlexicalAsset, ...]   # registry order (nonlexical.ASSET_IDS)
nonlexical_asset(asset_id: str) -> NonlexicalAsset   # KeyError for an unknown ID
calibration_example(profile: Profile | str) -> NonlexicalAsset
build_reserved_registry() -> ReservedRegistry        # what sound/reserved/registry.json equals
```

| ID | Samples | Consumer |
| --- | --- | --- |
| `calibration-P1`, `calibration-P2`, `calibration-P3` | 96,000 (2.000 s) | Comfort and gain screen, Study B screening and profile menu (#64, #70) |
| `ready-cue` | 15,360 | Grammar familiarization (#68) |
| `click-action` (single), `click-target` (double) | 192, 4,032 | Grammar screen (#68) |
| `click-grammar-demo` | 13,824 | Action click + 9,600 zero samples + target click (#68) |

- `NonlexicalAsset`: `id`, `kind` (`calibration`, `ready_cue`, `click`), `profile`
  (`None` = every profile), `samples` (read-only int64), `segments` (sounding
  `(onset, n_samples)` pairs), `description`, `.n_samples`, `.pcm`, `.pcm_sha256`,
  `.file_sha256`, `.peak`, `.peak_dbfs`, `.rms_dbfs`, `.active_rms_dbfs`,
  `.to_entry()`. `write_wav(asset, path)` writes the canonical WAV.
- Every asset is in `sound/reserved/registry.json` (`recipe: null`). No asset has a
  motif length, so no recipe renders to one; `validate()` reports `E_RESERVED` on an
  exact waveform match.
- Unity (#64) can verify a WAV against the registry's `file_sha256` (or
  `pcm_sha256`) like any package WAV. The assets set no sound pressure; the
  comfortable gain is set on the calibration example, which has the motif RMS.

## Vocabulary store (#11)

Append-only store of committed atoms: format, verification and storage policy in
[`sound/docs/store.md`](../../sound/docs/store.md); log record format
`sound/schema/store-record.schema.json`. Entries can be added, never changed; there
is no update and no delete.

```python
VocabularyStore(root: str | os.PathLike[str], *,
                clock: Callable[[], datetime] | None = None,        # default: UTC now
                reserved: ReservedRegistry | Iterable[ReservedEntry] | None = None)
.create_book(book_id: str, profile: Profile | str, *, kind: str = "study",
             threshold: Fraction | Decimal | int | str | None = None) -> str   # chain head
.commit(book_id: str, atom_id: str, semantic_label: str | None,
        recipe: Recipe | Mapping[str, Any] | str | bytes, *, source: str,
        profile: Profile | str | None = None, pcm_sha256: str | None = None,
        references: Iterable[Reference] | None = None) -> tuple[StoreEntry, str]
.get(book_id, atom_id) -> StoreEntry
.list(book_id) -> list[StoreEntry]                    # commit order
.verify(book_id, *, rerender: bool = True, expected_head: str | None = None) -> VerifyReport
.snapshot_hashes(book_id) -> dict[str, str]           # {atom_id: pcm_sha256}, commit order
.snapshot(book_id) -> dict[str, dict[str, Any]]       # + recipe_sha256, profile, semantic_label
.freeze(book_id) -> str                               # chain head; idempotent
.books() -> list[str]; .book(book_id) -> BookInfo; .head(book_id) -> str
.records(book_id) -> list[dict[str, Any]]             # decoded log records
persistence_violations(before, after) -> tuple[str, ...]
snapshot_digest(snapshot) -> str
```

- Layout: `blobs/<pcm_sha256>.wav` (canonical WAV, written once, read-only) and
  `books/<book_id>/log.jsonl` (one canonical JSON record per line; `seq`,
  `prev_sha256`, `record_sha256`). The chain head is the SHA-256 of the last line.
- Events: `create_book`, `commit`, `recommit_noop`, `overwrite_rejected`,
  `commit_rejected_frozen`, `freeze`.
- `kind`: `study`, `fallback` (#15; entries have `semantic_label=None`) or
  `synthetic` (IDs `DEMO-...`). Study and fallback books cannot be created inside the
  repository working tree (`E_POLICY`). Book IDs: 3-64 letters, digits and inner
  hyphens; no `A1`/`A2`/`A3` token or method word.
- `commit` order of checks: frozen book -> log `commit_rejected_frozen`, raise
  `BookFrozen`; committed atom -> identical recipe, profile, waveform and label logs
  `recommit_noop` and returns the existing entry, anything else logs
  `overwrite_rejected` and raises `OverwriteRejected` (`.reasons` from `profile`,
  `recipe`, `semantic_label`, `waveform`); new atom -> `validate()` against the
  book's entries in commit order (+ `references`) with the book's threshold and the
  reserved signals; failure raises `CommitRejected(result)` and logs nothing.
- `semantic_label` must be an ontology label of the atom's family and role
  (`av_sound.store.SEMANTIC_LABELS`), unique in the book (`E_LABEL`).
- `profile=` and `pcm_sha256=` are assertions: for a new atom a mismatch raises
  (`E_PROFILE`, `E_WAVEFORM`); for a committed atom it is an overwrite attempt.
- `StoreEntry`: `book_id`, `atom_id`, `family`, `role`, `matrix_index`,
  `semantic_label`, `recipe`, `profile`, `pcm`, `pcm_sha256`, `file_sha256`,
  `n_samples`, `renderer_version`, `validator_version`, `threshold`, `source`,
  `timestamp`, `commit_index` (the nearest-reference atom index), `seq`;
  `.reference()` -> `Reference(atom_id, ...)`. It satisfies `AtomAudioLike`.
- Errors (`StoreError` with `.code`): `InvalidIdentifier` (`E_IDENTIFIER`),
  `NotFound`, `BookExists`, `CommitRejected` (`.result`), `OverwriteRejected`
  (`.record`, `.chain_head`), `BookFrozen` (`.record`, `.chain_head`),
  `StoreIntegrityError` (`.issues`), and `StoreError` with `E_POLICY`, `E_VERSION`,
  `E_PROFILE`, `E_LABEL`, `E_WAVEFORM`.
- Every read and write first checks the chain, the records and the blobs, and refuses
  a damaged book (`StoreIntegrityError`). `VerifyReport`: `ok`, `issues`
  (`VerifyIssue(code, message, seq, atom_id)`), `codes`, `n_records`, `n_entries`,
  `frozen`, `chain_head`, `rerendered`, `anchored_seq`. Removing lines from the end is
  detected only with `expected_head`: callers record the chain heads that `commit`
  and `freeze` return (#20 batch log; release manifest).
- Publish only chain heads and `snapshot_digest` values of study books, never
  per-atom hashes (the recipe domain can be enumerated).

## Fallback (#15), packages (#13)

*Pending.* Each pull request adds its section here: `scan_fallback()` and the
package builder.
