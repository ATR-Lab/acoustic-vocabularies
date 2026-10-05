# Fallback banks and fallback books

Builder version **0.1.0** (`av_sound.fallback.BUILDER_VERSION`), manifest version 1.
Status: **pilot default; rebuilt and frozen at G4** with the renderer, the validator
and the separation threshold. Producer: #15 (`av_sound.fallback`). Consumers: round
orchestrator (#20), audit reports (#24), G4 freeze (#25).

## 1. What the protocol needs

| Need | Source | Implementation |
| --- | --- | --- |
| A frozen ordered bank of 64 admissible recipes per profile, separate from all study outcomes | Study A protocol §3.7 | `build_bank`: code and a seed only |
| One complete 16-atom fallback book per profile that passes all pairwise checks | §3.7 | `build_book` |
| An atom with no eligible candidate after 12 slots gets the first unused bank recipe that passes the current book's checks; the scan is recorded apart from the 12 slots | §3.7 | `scan_fallback`, scan record (`fallback-scan.schema.json`) |
| If no bank recipe passes, the complete fallback book replaces the book, which is flagged as failed generation and keeps its method label | §3.7 | `ScanResult.exhausted`; #20 substitutes and flags |
| Uniform sampling from the declared categorical values with a stored seed | §3.5 (A2 round 1), §3.2 domain | the stream in section 2 |
| The threshold may change at G4; banks and books are then rebuilt | #15, #25 | section 8 |

Out of scope: Study B has no fallback (Study B protocol §4).

## 2. The draw stream

Every draw is a pure function of `(seed, profile, purpose, draw)`. Another
implementation can reproduce it from this section.

1. **Key**: the ASCII bytes of `av-sound/fallback/v1|<seed>|<profile>|<purpose>|<draw>`.
   `profile` is `P1`, `P2` or `P3`; `purpose` is `bank` or `book`; `draw` is the
   decimal draw number from 0, without leading zeros. Example:
   `av-sound/fallback/v1|DEMO-fallback-v1|P1|bank|0`.
2. **Bytes**: block `k` (k = 0, 1, ...) is `SHA-256(key || uint64_le(k))`; the stream
   is block 0, then block 1, and so on, one byte at a time.
3. **Uniform index** in `[0, n)`: take the next byte `b`. If `b < 256 - (256 mod n)`,
   the index is `b mod n`; otherwise take the next byte. (n = 4: no byte is
   rejected; n = 13: bytes 247-255 are rejected; n = 3: byte 255 is rejected.)
4. **Recipe**: 12 indices in this order, each into the allowed values in ascending
   order: `total_ms` (450, 600, 750, 900), `pitches[0..2]` (-6 .. +6),
   `rhythm_weights[0..2]` (1 .. 4), `gaps_ms[0..1]` (20, 40, 60),
   `amplitudes[0..2]` (0.6, 0.8, 1.0).

Every recipe of the domain has the same probability, (1/4) x (1/13)^3 x (1/4)^3 x
(1/3)^2 x (1/3)^3. The draw is not rejection-sampled for admissibility: the
validator decides (section 3). `draw_recipe(seed, profile, purpose, draw)` returns it.

**Seeds** are 1-128 characters of `[A-Za-z0-9._-]`. A seed that starts with `DEMO-`
is a public example. Any other seed is restricted and needs at least 32 characters:
create it with `python -c "import secrets; print(secrets.token_hex(32))"` and keep it
in restricted storage. The manifest records only the **seed fingerprint**,
`SHA-256("av-sound/fallback/seed/v1|<seed>")`, so a rebuild can be matched to its
seed without revealing it.

## 3. Construction

Both builds use the greedy rule. For draw 0, 1, 2, ...: call
`validate(recipe, profile, kept, reserved=<registry>, threshold=<threshold>)`, where
`kept` is the list of recipes accepted so far. Keep the recipe when the result is
`ok`. This checks short events, clipping, the reserved signals (`E_RESERVED`), an
identical waveform (`E_DUPLICATE`) and the separation threshold (`E_SEPARATION`)
against every recipe already kept. Every draw is logged with its codes.

| | Bank | Book |
| --- | --- | --- |
| Stream purpose | `bank` | `book` |
| Stops at | 64 accepted recipes | 16 accepted recipes |
| Order | acceptance order: index 0 is scanned first | stored order: the `j`-th accepted recipe is atom `ATOM_IDS[j]` (`K-a1` .. `K-a4`, `K-r1` .. `K-r4`, `Q-a1` .. `Q-r4`) |
| Result | 64 x 63 / 2 = 2,016 separated pairs, distinct waveforms | all 120 pairs separated, distinct waveforms |

`threshold=None` reads `sound/config/validator.json`; `reserved=None` reads
`sound/reserved/registry.json`. A build that cannot reach its size within
`max_draws` (default 100,000) fails with `E_EXHAUSTED`. Example: a bank at threshold
0.4 cannot reach 64 recipes. The threshold must be an exact decimal (`"0.10"`).

The bank and the book are built independently. They are not separated from each
other (decision D3, section 9).

## 4. Manifest and hashes

`FallbackSet.manifest()` follows
[`../schema/fallback-manifest.schema.json`](../schema/fallback-manifest.schema.json).
It lists, for P1, P2 and P3 in this order, the bank (64 entries: `index`, `draw`,
`recipe`, `recipe_sha256`, `pcm_sha256`, `file_sha256`) and the book (16 atoms:
`atom_id`, `position`, `draw`, and the same fields), and these set fields:
`builder_version`, `stream`, `seed_kind` (`demo` or `restricted`), `demo_seed` (`null`
when restricted), `seed_fingerprint`, `threshold`, `renderer_version`,
`validator_version`, `reserved_sha256` and `bank_size`.

| Hash | Definition |
| --- | --- |
| `fallback_bank_hash` | SHA-256 of the compact canonical JSON (sorted keys, separators `,` and `:`, ASCII) of the manifest without the `fallback_bank_hash` field. This value goes into the apparatus manifest and the G4 freeze list. |
| `bank_sha256` | SHA-256 of the compact canonical JSON `{"entries": [[recipe_sha256, pcm_sha256], ...], "profile": "P1"}` |
| `book_sha256` | `snapshot_digest({atom_id: pcm_sha256})`. It equals the `snapshot_sha256` of the frozen store book. |
| `reserved_sha256` | SHA-256 of the compact canonical JSON list of the reserved entries. This is the same value as the store's `reserved_sha256`. |
| `seed_fingerprint` | Section 2 |
| `recipe_sha256`, `pcm_sha256`, `file_sha256` | Renderer spec D9 |

`load_fallback(path_or_dict)` checks the schema, `fallback_bank_hash`, every recipe
hash, both digests, the draw numbers and the canonical form. A manifest with another
renderer or validator version fails with `E_VERSION`; rebuild it instead.
`verify_fallback(...)` re-renders every recipe. It also re-runs the validator in
acceptance order and returns a list of problems. G4 (#25) uses it with the frozen
renderer.

## 5. Scan rule (`scan_fallback`)

```python
result = scan_fallback(bank, book_entries, used=used_indices, threshold=book_threshold)
```

- `bank`: the frozen `FallbackBank` of the book's profile.
- `book_entries`: the book's committed entries, in commit order. These are
  `StoreEntry` objects from `store.list(book_id)` or validator `Reference` objects.
- `used`: the bank indices already assigned in this book. They are skipped and logged
  as `used`.
- `threshold`: the book's threshold. `reserved=None` reads the registry.

The scan takes the bank in index order. It calls `validate()` for each unused recipe
against the book and stops at the first recipe that passes. `ScanResult.selected` is
that `BankEntry`, or `None` (`exhausted`) if no recipe passes. `ScanResult.log` has
one `ScanStep` (`index`, `recipe_sha256`, `pcm_sha256`, `outcome` `used` / `rejected`
/ `selected`, `codes`, `messages`) for every recipe it looked at. When it finds none,
the log has all 64 steps. `ScanResult.to_dict()` is the scan record
([`../schema/fallback-scan.schema.json`](../schema/fallback-scan.schema.json)). The
orchestrator logs it apart from the 12 slot records. The scan changes nothing. A
bank recipe that does not render to its recorded waveform raises `E_STALE`.

The orchestrator (#20) then does one of these:

- **Selected**: commit the recipe to the assigned book with the atom's meaning:
  `store.commit(book_id, atom_id, label, sel.recipe, source=sel.source,
  pcm_sha256=sel.pcm_sha256)`. `sel.source` is `fallback-bank-P1-07`. Log the atom as
  a per-atom fallback for the audit (#24).
- **Exhausted**: flag the book `failed_generation`. Commit the 16 atoms of
  `fallback.book(profile)` in stored order, with the batch's meanings, to a new
  store book that keeps the method label in the allocation key. Use
  `source=atom.source` (`fallback-book-P1-K-a1`). Then mark the failed book with
  `store.void(book_id, cause="failed_generation", reason=..., superseded_by=<new
  book>)`. Nothing is deleted, so the failed book stays archived.

## 6. Freezing and storage

The real seed, the manifest, the build log and the WAVs of a restricted build are
**study material**. They can be heard by learners. Keep them in restricted storage
and never in git ([`store.md`](store.md), section 6). Publish **only
`fallback_bank_hash`** (apparatus manifest, G4). If an anchor is needed, also publish
the chain heads of the frozen fallback books.

- `write_fallback(fset, out_dir)` writes `fallback-manifest.json`,
  `fallback-build-log.json` (every draw and its codes),
  `<profile>/bank/<NN>.json|.wav` and `<profile>/book/<atom_id>.json|.wav`. The
  files are created once and are read-only. `out_dir` must be empty. A
  restricted-seed set is refused inside any git work tree (`E_POLICY`).
- `freeze_fallback_books(store, fset)` commits each book to a store book of kind
  `fallback` with ID `FB-<profile>-<first 12 hex of fallback_bank_hash>`. The entries
  have no meaning and the waveform is asserted. Each write is anchored with the
  previous chain head (`expected_head`). Then it freezes the book. The store refuses
  fallback books inside any git work tree (`E_POLICY`). A store book holds at
  most the 16 atom IDs, so the banks are not store books: they are frozen as the
  hashed manifest and read-only files.
- Public examples use the seed `DEMO-fallback-v1`. Only the JSON manifest
  [`../testvectors/fallback/demo-manifest.json`](../testvectors/fallback/demo-manifest.json)
  is committed (`seed_kind: "demo"`). It is not study material. Tests rebuild it on
  Linux, macOS and Windows.

## 7. Tool

```bash
# Public example (writes or checks the committed DEMO manifest)
uv run --project sound python sound/tools/build_fallback.py --demo-seed DEMO-fallback-v1 \
    --manifest sound/testvectors/fallback/demo-manifest.json
uv run --project sound python sound/tools/build_fallback.py --demo-seed DEMO-fallback-v1 \
    --check sound/testvectors/fallback/demo-manifest.json

# Restricted pilot or confirmatory build (seed file and output in restricted storage)
uv run --project sound python sound/tools/build_fallback.py \
    --seed-file <restricted>/fallback-seed.txt --out <restricted>/fallback-<date>
```

The tool prints the counts, the bank and book digests and `fallback_bank_hash`.
`--out` also builds `<out>/store` with the three frozen fallback books. Then it
verifies the store against the returned chain heads, re-renders every recipe and
reports any problem. The tool refuses these cases:

- a seed file or restricted output inside a git work tree
- a `DEMO-` seed in a seed file
- a restricted seed shorter than 32 characters
- `--out` inside the repository
- overwriting a restricted manifest

## 8. Rebuild after the G4 threshold decision

If O6.2.2 and G4 change the threshold, or the renderer or validator version
changes, do these steps:

1. Update `sound/config/validator.json` (or the version).
2. Regenerate the DEMO manifest with `--manifest`. The test fails until you do.
3. Run the restricted build again from the stored seed into a new directory.
4. Check it with `verify_fallback`.
5. Record the new `fallback_bank_hash` in the apparatus manifest and the freeze
   manifest.

The seed does not change. The same seed and settings always give the same hashes.

## 9. Decisions (pilot defaults)

- **D1. Stream.** A SHA-256 counter stream per draw, keyed by seed, profile, purpose
  and draw number. Any draw can be reproduced alone. The stream is the same on every
  platform and does not depend on Python's `random` or numpy. It has the same form
  as the READY-cue noise ([`nonlexical.md`](nonlexical.md), section 3.2).
- **D2. Stored atom order.** The book order is the acceptance order, mapped onto the
  canonical `ATOM_IDS`. Meanings are not part of a fallback book: they come from the
  batch permutation when the book is used.
- **D3. Bank and book are not separated from each other.** They are never in the
  same book. A bank recipe repairs one atom of an assigned book. The fallback book
  replaces a whole book, including any bank recipes already in it. `scan_fallback`
  checks every bank recipe against the actual book when it is used. Coupling the two
  would add a constraint that the protocol does not ask for, and one could no longer
  be rebuilt without the other. DEMO: no shared waveform, and the smallest bank-book
  distance is 0.149 to 0.198.
- **D4. "Unused" is per book.** `used` holds the bank indices already assigned in the
  current book. A book-independent scope would make one method's fallback depend on
  another method's book (Study A protocol §3.1 keeps the methods independent). In
  a book, a reused recipe would fail `E_DUPLICATE` anyway; `used` makes the log
  explicit. #20 owns the final choice.
- **D5. Thresholds.** The bank and book use the build threshold. A scan uses the
  book's threshold and records both.

## 10. DEMO example (seed `DEMO-fallback-v1`, threshold 0.1)

| Profile | Bank draws | `bank_sha256` | Book draws | `book_sha256` | Rejections |
| --- | --- | --- | --- | --- | --- |
| P1 | 71 | `e23b83642ba19871cbb13d80b0f7ad94753314294b5885e6058ad6a81d36f18d` | 16 | `0b38a2bfb78a8d47ccd6de55ea8c5661819567d77049216e77527f210883fdad` | `E_EVENT_SHORT` 7 |
| P2 | 70 | `3cf26c5309a53b56f1f4182e80608bcad0f83c43c5fd2ed5955c8497ce1576aa` | 17 | `bcaa4a6ca29a6a4d973276b7ffb9ae6825b191886826713113db66b640db54e4` | `E_EVENT_SHORT` 7 |
| P3 | 69 | `99563251fd2ce9ff0122423071369cb3c878001281e27a03643344b5682ec825` | 16 | `bfb26ea3347cfab29fea1c3aae5a01492815229abdeadea71095efbb163c24d2` | `E_EVENT_SHORT` 5 |

`fallback_bank_hash` = `549f0c47e5435dab92bd6a2bec2cf41563c270545cded9ab4355e25887ff7571`.
The build takes about 0.4 s on an Apple-silicon laptop.

At 0.10, uniform draws are far apart, so only short events are rejected. The
smallest distance in a DEMO bank is 0.154 to 0.158, and in a book 0.254 to 0.298. At
0.25 the separation screen rejects some draws (P2 bank: 9 `E_SEPARATION`, 8
`E_EVENT_SHORT`, in 81 draws).
