# Golden files and cross-machine determinism

Golden manifest format **1.0.0** (`av_sound.golden.FORMAT_VERSION`). Producer: #12
(`av_sound.golden`, [`../tools/make_goldens.py`](../tools/make_goldens.py)).
Consumers: package builder #13 (green determinism status), G4 freeze #25 (the
manifest freezes with renderer `1.0.0`), and every pull request that touches
`sound/`.

Study A protocol §3.2 requires the hash of the stored waveform, because a seed alone
does not reproduce it. A stored hash can stand for the sound only if every machine
(generation host, analyst laptop, CI) makes the same bytes. The golden set shows that
the renderer, the composer, the nonlexical assets and the vocabulary store do this on
Linux, macOS and Windows, on x86_64 and arm64, and CI keeps it that way.

## 1. Golden set

All inputs are synthetic. None is a study motif, book or package.

| Category | Items | Contents |
| --- | --- | --- |
| `recipe` | 183 | 61 recipes (below) rendered for P1, P2 and P3 |
| `atom` | 48 | The 16 atoms of each synthetic book `DEMO-P1` .. `DEMO-P3` (`av_sound.synthetic`) |
| `message` | 96 | All 32 messages of each book: 18 trained (composed, with a WAV) and 14 held out (`composite_hash` only, no audio, as in a package) |
| `nonlexical` | 7 | The reserved assets (#14): calibration examples, READY cue, clicks |
| `store` | 3 | One round trip per book through `VocabularyStore` (section 1.2) |
| All | 337 | |

### 1.1 Recipes

`av_sound.golden.GOLDEN_RECIPES` covers the corners of the recipe domain:

| Block | Recipes | Covers |
| --- | --- | --- |
| `t<T>-pitch-<shape>` | 20 | Every `total_ms` x pitches (-6, -6, -6), (0, 0, 0), (+6, +6, +6), (-6, 0, +6), (+6, 0, -6) |
| `gaps-<g1>-<g2>` | 9 | Every gap pair; weights 2/2/3 make rounded event lengths |
| `amps-<a1>-<a2>-<a3>` | 13 | The three uniform triples, all six permutations of 0.6/0.8/1.0, four triples with two equal values |
| `weights-*`, `shortest-event-*`, `rounding-*`, `short-event-*`, `longest-rejected-event` | 13 | Weights 1-1-1 and 4-4-4; one long event in each position; the shortest legal event (2,880 samples) in each position; event 1 rounded up and down (spec D1); short events of 1,760 samples and the longest rejected event (2,812 samples), rendered with `short_event` set |
| `pitch-sweep-*`, `pitch-wide-jumps` | 4 | The pitch values -5 to -1 and +1 to +5; jumps of 12 semitones |
| `spec-worked-example`, `worst-crest-factor` | 2 | Renderer spec section 5 (hashes pinned in the test) and the D6 worst case (peak 20,238 LSB at P1) |

Every pitch value, every weight in every position, every gap pair and every amplitude
in every position occurs. Two groups share a waveform by design, and the test checks
that they are the only ones: the three uniform amplitude triples (spec D5) and weights
1-1-1 and 4-4-4 (the same sample layout, spec D1).

### 1.2 Store round trip

For each synthetic book, in a temporary directory: `create_book` (kind `synthetic`,
threshold `"0.10"`, a fixed clock from 2026-01-01T00:00:00Z with 1 s per record, no
reserved signals), commit the 16 atoms in `ATOM_IDS` order with the identity meaning
permutation, `freeze`, then read back. The item records the chain heads after
`create_book`, after the commits and after `freeze`, the `snapshot_digest`, and
whether:

- every read-back entry hashes to its `pcm_sha256`, its blob file to its `file_sha256`,
  and its samples and recipe equal what was committed (`readback_ok`);
- the snapshot equals the atom goldens (`atoms_match`);
- all 32 messages composed from the stored entries have the message golden hashes
  (`messages_match`);
- `verify(rerender=True, expected_head=<freeze head>)` passes (`verify_ok`).

The explicit threshold keeps the separation config (#9) out of the goldens.

## 2. Manifest

[`tests/golden/manifest.json`](../../tests/golden/manifest.json): indented JSON with
sorted keys and LF line endings (`.gitattributes` keeps `tests/golden/**` byte-exact).

| Field | Value |
| --- | --- |
| `format`, `format_version`, `synthetic` | `"av-sound golden manifest"`, `"1.0.0"`, `true` |
| `renderer_version`, `renderer_hash` | The renderer the hashes come from (spec D10) |
| `validator_version`, `asset_spec_version`, `store_record_version` | Other versions that shape item bytes |
| `version_rules` | Per category, the version fields that may justify a changed item (section 4) |
| `counts`, `digests` | Items per category; SHA-256 of the compact canonical JSON of the items, overall (`all`) and per category |
| `items[]` | `id` (`<category>/...`), `category`, `inputs`, `outputs` |

`inputs` is everything an item is computed from (recipe and profile, book and atom
IDs, asset ID, store settings). `outputs` holds the hashes and facts that must not
change: `pcm_sha256` and `file_sha256` (renderer spec D9; `file_sha256` is `null` for
held-out messages), `n_samples`, event lengths, flags, peaks, chain heads.
`verify_manifest()` recomputes every item from the manifest's own `inputs`, so the
check does not depend on how the manifest was built.

## 3. Tests and CI

`tests/golden/` (run with `uv run --project sound pytest --import-mode=importlib tests/golden`):

- every golden hash reproduces on this machine, and the manifest equals a fresh build
  from the definitions in code;
- rendering the whole set a second time, in reverse order, gives identical items,
  samples and digests;
- a one-sample change to one rendered motif (a patched renderer) is reported for that
  item only; a one-sample change to one atom is reported for the atom, its four
  messages and the store round trip of its book;
- a one-sample change to a golden WAV file is reported (file hash and first differing
  sample), as are stray, missing, truncated and Git LFS pointer files;
- committed WAVs under `tests/golden/wav/` are byte-compared (skipped while there are
  none; section 5);
- coverage of the domain corners, the spec examples, held-out messages without audio,
  agreement with the composition vectors, the reserved registry and the store growth
  vectors;
- the version guard and the cross-runner comparison on synthetic manifests and a
  temporary git history.

[`.github/workflows/sound-golden.yml`](../../.github/workflows/sound-golden.yml) runs on
every pull request that touches `sound/` or `tests/golden/`, and on `main`:

| Label | Runner | OS and CPU |
| --- | --- | --- |
| `linux-x86_64` | `ubuntu-latest` | Ubuntu, x86_64 |
| `linux-arm64` | `ubuntu-24.04-arm` | Ubuntu, arm64 |
| `macos-arm64` | `macos-latest` | macOS, arm64 |
| `macos-x86_64` | `macos-15-intel` | macOS, x86_64 |
| `windows-x86_64` | `windows-latest` | Windows, x86_64 |
| `windows-arm64` | `windows-11-arm` | Windows, arm64 |

Every job uses the same uv-managed CPython 3.11.15 build, native to the runner's CPU
(`UV_PYTHON`, `UV_PYTHON_PREFERENCE=only-managed`; without it, uv runs x86_64 Python
under emulation on Windows arm64). Every dependency, numpy included, comes from
`sound/uv.lock`. Each job runs `tests/golden`, then `make_goldens.py --check` with
`--digest-out`, which writes the job's digests and platform to the artifact
`golden-digest-<label>` and to the job summary. The `compare` job downloads all
records, prints one table row per runner and fails if a runner is missing, if any
digest differs between runners or from the manifest, if a runner reported a
mismatch, or if a runner's CPU (`platform.machine()`) is not the architecture in its
label. The Linux x86_64 job uploads the WAVs as
the artifact `golden-wav` (14 days) for listening and inspection. `sound.yml` also
runs `tests/golden` with coverage on its three-OS matrix.

If a hash differs between platforms, it is a renderer defect. Fix the arithmetic (the
sample path is integer-only, renderer spec D4); do not loosen the test and do not give a
platform its own goldens.

## 4. Changing goldens

A deliberate change to rendered bytes bumps `RENDERER_VERSION` and regenerates the
goldens in the same pull request, with a reviewer note that explains the change
(renderer spec D10):

```bash
uv run --project sound python sound/tools/make_goldens.py          # rewrite the manifest
uv run --project sound python sound/tools/make_goldens.py --check  # what CI runs
```

On pull requests the `guard` job runs
[`../tools/check_golden_bump.py`](../tools/check_golden_bump.py) `origin/<base>` with full
history. It compares the manifest at the merge base with the pull request:

- New items pass.
- A changed or removed item fails unless a version field that governs its category
  increased. The rules are read from the base manifest, so a pull request cannot relax
  them:

| Category | Version fields that may justify a change |
| --- | --- |
| `recipe`, `atom`, `message` | `renderer_version` |
| `nonlexical` | `renderer_version`, `asset_spec_version` (`nonlexical.md` section 7) |
| `store` | `renderer_version`, `validator_version`, `store_record_version` (`store.md`) |

- A version field that changes must increase. Deleting the manifest fails.
- The report (job summary) lists every changed item and asks for the reviewer note.
  A change to `renderer_hash` alone (code changed, bytes did not) passes with a note.

The synthetic books (`av_sound.synthetic`) are inputs of the atom, message and store
goldens. Do not change them; add new items instead.

## 5. WAV files and Git LFS

The issue asks for golden WAVs in Git LFS. LFS uploads are disabled for this
repository, and the repository guard rejects WAV files that are not LFS pointers, so
**only hashes are committed**. The WAVs are rebuilt from code
(`make_goldens.py --wav-dir OUT`, which also checks them) and CI publishes them as the
`golden-wav` artifact. Because every byte is a function of the code and the manifest,
the hashes lock the bytes as tightly as stored files would.

When LFS is enabled, the WAVs can be added without code changes:

1. `make_goldens.py --wav-dir tests/golden/wav` writes `tests/golden/wav/<item id>.wav`
   (for example `recipe/spec-worked-example/P2.wav`); the existing `*.wav` LFS rule
   applies. Remove the copied `manifest.json` from that directory.
2. Add `lfs: true` to the checkout step in `sound-golden.yml`.
3. `test_committed_golden_wavs_match` then byte-compares every committed file with a
   fresh render and its `file_sha256`. A pointer that was not fetched fails with a
   message that says so.
