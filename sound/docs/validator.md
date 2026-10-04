# Validator and separation screen

Validator version **0.1.0**. Status: **pilot default; freezes at G4** with the
renderer and the separation threshold.

`av_sound.validate()` is the one admissibility check for every proposer: A1, A2, A3
and the Study B bank builder (Study A protocol §3.2, Study B protocol §4). It
implements the checks that renderer spec D11 assigns to the validator. It never
edits or repairs a recipe.

```python
from av_sound import Profile, Reference, render, validate

committed = [Reference.from_rendered("K-a1", render(recipe_a1, Profile.P2))]
result = validate(candidate_json_text, Profile.P2, committed)
result.ok, result.codes, result.nearest_id, result.nearest_distance
```

## Reason codes

`ValidationResult.codes` lists every failing code, always in this order.
`messages[i]` explains `codes[i]`. `primary_code` is `codes[0]`.

| # | Code | Fails when |
| --- | --- | --- |
| 1 | `E_JSON` | Text is not strict JSON (the `Recipe.from_json` rule): syntax error, `NaN`/`Infinity`, duplicate keys; bytes that are not UTF-8 or start with a byte-order mark |
| 2 | `E_SCHEMA` | Not an object, missing or extra field, wrong type or array length, or an integer field given as a number with a fraction part (`600.0`, spec D11) |
| 3 | `E_DOMAIN` | A value outside its allowed set (JSON Schema `enum`) |
| 4 | `E_EVENT_SHORT` | Any event shorter than 2,880 samples (60 ms) after rounding (spec D1) |
| 5 | `E_NONFINITE` | The renderer reports non-finite samples |
| 6 | `E_CLIP` | The renderer reports overflow after normalization (spec D7) |
| 7 | `E_DUPLICATE` | `pcm_sha256` equals that of a committed reference |
| 8 | `E_RESERVED` | Collision with a reserved signal (see below) |
| 9 | `E_SEPARATION` | 12-feature distance below the threshold to any committed reference |

Stages:

1. Parse and check the schema. Schema and domain errors use
   [`recipe.schema.json`](../schema/recipe.schema.json) with `jsonschema`: an `enum`
   error is `E_DOMAIN`, every other error is `E_SCHEMA`. An `enum` error on a value
   that already has a type error is not reported again. A single fault gets the same
   code as `RecipeError` from `Recipe`. In a mapping, integral values such as numpy
   integers count as integers, as in `Recipe`. If stage 1 fails, the result lists only
   these codes; `recipe`, `features`, `pcm_sha256` and the nearest reference are
   `None`.
2. Otherwise render once and evaluate **all** remaining checks. A recipe with a short
   event is still checked for duplicates, reserved signals and separation.

`E_NONFINITE` and `E_CLIP` cannot happen inside the recipe domain: the renderer uses
integers, and the worst case has 4.19 dB of headroom (spec D6). The checks stay
because the protocol requires them. Tests inject both conditions (a patched
`render` that sets `nonfinite`; a raised `RMS_TARGET` that causes overflow). When
either flag is set, `pcm_sha256` is `None` and the duplicate and hash-based reserved
checks are skipped; the feature checks still run.

A slot ledger that needs one outcome per slot uses `primary_code`. Suggested mapping
to the #17 outcome codes: `E_JSON` -> `invalid_json`, `E_SCHEMA` ->
`schema_violation`, `E_DOMAIN` -> `out_of_domain`, `E_EVENT_SHORT` ->
`event_too_short`, `E_NONFINITE` -> `render_fail`, `E_CLIP` -> `clipping`,
`E_DUPLICATE` -> `duplicate`, `E_RESERVED` -> `reserved_collision`,
`E_SEPARATION` -> `separation_fail`.

Caller errors raise instead of giving a result: an unknown profile, a `committed`
item that is not a `Reference`, a reference with another profile (`ValueError`), an
inexact threshold (a float), or a reserved registry built with another renderer
version.

## Committed references

`committed` is the ordered list of `Reference(ref_id, recipe, pcm_sha256, profile)`
objects for one book and one profile: both families and both roles. The position in
the list is the atom index. For Study B, pass the retained options of the other
atoms under that profile. Distinct waveforms within one cell are a separate check
on `result.pcm_sha256`. Every reference must have the candidate's profile.

`nearest_reference(candidate, committed)` returns the reference with the smallest
exact squared feature sum. Ties go to the lowest index. It returns `None` when
nothing is committed (the first atom). `validate()` reports the same reference in
`nearest_id`, `nearest_index` and `nearest_distance`.

## Features and exact separation

The 12 features (Study A protocol §3.2) are exact `Fraction`s in [0, 1]:

| Features | Formula |
| --- | --- |
| 3 pitches | `(p + 6) / 12` |
| total | `(T - 450) / 450` |
| 3 proportions | `(q - 1/9) / (2/3 - 1/9)`, `q = w / sum(w)` |
| 2 gaps | `(g - 20) / 40` |
| 3 amplitudes | `(a - 0.6) / 0.4`, with `a` read as the exact decimal 0.6, 0.8 or 1.0 |

The distance is `sqrt(sum((x_j - y_j)^2) / 12)`. The decision does not use a float:
a candidate passes when `sum((x_j - y_j)^2) >= 12 * t^2`, computed with fractions.
So a distance equal to the threshold passes and anything below fails, on every
platform. `distance()` returns a float for reports and tools only (one correctly
rounded conversion, then `math.sqrt`).

**No domain pair is at distance exactly 0.10.** The proportion features depend on
the three weights together; every other feature depends on one field. So the set of
achievable squared sums is exactly {P + R}, with P over all 64 x 64 weight pairs and
R over the other nine coordinates. An exhaustive search
([`../tools/separation_boundary.py`](../tools/separation_boundary.py), repeated in
`tests/sound/test_separation_features.py`) finds no pair with sum 3/25. The nearest
pairs are at 0.0999491 (sum 10789/90000) and 0.1000236 (sum 10589/88200). Thresholds
0.125, 0.15 and 0.25 are exactly achievable. The fixtures are in
[`../testvectors/validator/boundary.json`](../testvectors/validator/boundary.json).

## Threshold configuration

The threshold is read from [`../config/validator.json`](../config/validator.json)
(schema `validator-config.schema.json`). It is a decimal string (`"0.10"`), parsed to
the exact fraction 1/10. A `threshold=` argument overrides it for tools and tests
(`str`, `Fraction`, `Decimal` or `int`; floats are refused). The value is a pilot
default. The separation-threshold listening check (#23, O6.2.2) keeps or revises it
before G4 (#25). One value applies to every method and to Study B banks.

## Reserved signals

[`../reserved/registry.json`](../reserved/registry.json) (schema
`reserved-registry.schema.json`) lists the calibration examples, the READY cue and
the clicks. #14 fills it; the file starts with no entries. Each entry has `id`,
`kind`, `profile` (or `null` for all profiles), `n_samples`, `pcm_sha256`,
`file_sha256`, `recipe` (or `null`) and `description`. A candidate gets `E_RESERVED`
when:

- its `pcm_sha256` equals an entry's, or
- the entry has a recipe, applies to the candidate's profile (same profile or
  `null`), and the candidate is closer to it than the separation threshold.

The registry records the renderer version of its hashes. `validate()` refuses a
registry built with another renderer version, because its hashes would be stale.

## Result record

`ValidationResult.to_dict()` matches
[`../schema/validation-result.schema.json`](../schema/validation-result.schema.json)
(`result_version` 1). Features are exact text (`"0"`, `"1"`, `"p/q"`). The threshold
is exact text (`"0.1"`). The record carries `validator_version` and
`renderer_version` for the store (#11). `result.rendered` holds the render used for
the checks, so the caller does not render again.

## Performance

`sound/tools/bench_validate.py` validates one JSON candidate (T = 900 ms) against 15
committed references. On an Apple-silicon laptop the median is about 1.2 ms (render
and hash about 0.5 ms). The target is under 100 ms; a proposal slot is 40 s.

## Known limitations (for the threshold review)

- Amplitude features are absolute, but normalization (spec D6) removes the overall
  level. Moving all three amplitudes one step up changes each amplitude feature by
  0.5 (squared sum 0.75, distance 0.25 on its own), but only the small change in the
  amplitude ratios stays audible. Uniform triples render to identical bytes (spec D5) and get
  `E_DUPLICATE`. Near-proportional triples, such as (0.6, 0.8, 0.8) and
  (0.8, 1.0, 1.0), pass the screen although they sound almost the same (renderer
  spec §6). The listening check (O6.2.2) judges this; the protocol features are not
  changed here.
- The screen is an engineering check, not a perceptual guarantee.

## Versioning

`VALIDATOR_VERSION` changes whenever a decision can change for some input. The
threshold and the registry are data files with their own version fields. Every
store record keeps `validator_version`, `renderer_version` and the threshold.
