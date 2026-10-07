# Package format: learner (Study A) and dyad (Study B) packages

Format **1** (`format` = `av-sound/package`, `format_version` = 1). Producer: package
builder (#13, `av_sound.package`). Consumers: Unity audio subsystem (#64: file and hash
checks, playback, message composition), session engine (#67: hidden answers, held-out
IDs, schedules), independent package audit (O7.1.2).

Schema: [`sound/schema/package.schema.json`](../../sound/schema/package.schema.json). The
root schema is `manifest.json`; `$defs/answers`, `$defs/audio` and `$defs/allocation`
describe the other documents. A and B variants are selected by `study`. Message bytes and
hashes follow [`sound/docs/composition.md`](../../sound/docs/composition.md) (contract
1.0.0).

**Review status: Pending (human).** The schema and this contract need review by the owner
of #64 (and of #67 for `answers.json` and the slots).

## 1. What a package is

One package is the frozen bundle for one Study A book or one Study B dyad (Study A
protocol §3.7; Study B protocol §4-§7). It holds every audio file the app may play from
disk, the expected hash of every message, the hidden answers, and after sealing the
unit's permutation, the visit schedules and the allocation-dependent facts.

Rules that hold for every package:

- **Held-out messages never exist as complete audio.** No file contains a held-out
  message. The package carries its ID and expected composite hash only.
- **No method label, designer ID, rating, candidate history or store provenance.** The
  package ID is the anonymous book ID (A) or bank ID (B). Store `source` fields, recipes,
  timestamps, slot or attempt numbers are never copied (Study A protocol §2).
- **Everything is hashed.** `manifest.json` lists every other file with its SHA-256 and
  size, and one package hash covers the manifest. A directory with any other file is
  refused.
- **Deterministic.** No timestamps or random values: rebuilding from the same frozen
  book or bank gives the same bytes and the same package hash on every platform.
- **Storage.** Participant packages contain hidden answers and live in restricted
  storage; the builder refuses to write a non-DEMO package inside this repository.
  Only synthetic `DEMO-` packages appear here, and only their JSON files
  ([`sound/examples/package-demo/`](../../sound/examples/package-demo/)).

## 2. Layout

All paths are relative, use `/`, and are ASCII.

### Study A (one book)

```text
manifest.json                     integrity: file hashes and the package hash
answers.json                      hidden answers (meanings of atoms and messages)
audio.json                        audio index: atoms, trained messages, all 32 composite hashes
atoms/<atom_id>.wav               16 files: K-a1 .. K-a4, K-r1 .. K-r4, Q-a1 .. Q-r4
messages/<message_id>.wav         18 files: the trained messages only
permutation.json                  slot, added by seal() (#29)
allocation.json                   slot, added by seal() (restricted allocation facts)
schedules/<person_id>/<visit>.json  slot, added by seal() (#30): D0.json, D7.json
```

### Study B (one dyad bank)

```text
manifest.json, answers.json, audio.json
options/<profile>/<atom_id>-<rank>.wav   192 files: P1..P3 x 16 atoms x ranks 1..4
permutation.json, allocation.json        slots, added by seal()
schedules/<person_id>/<visit>.json       slot (#30): V1, V2, V3, W1, W4 per member
```

Ranks 1-3 are the displayed menu in stored order, rank 4 is the reserve (Study B
protocol §4). There are no message WAVs: the app composes every message at run time from
the committed options.

Every WAV is the canonical 44-byte-header WAV of the renderer spec (D8): mono, 16-bit,
48,000 Hz, no other chunks. Atoms have 21,600, 28,800, 36,000 or 43,200 samples; a
trained message has 52,800 to 96,000.

## 3. `manifest.json` and the package hash

| Field | Value |
| --- | --- |
| `format`, `format_version` | `av-sound/package`, `1` |
| `study` | `A` or `B` |
| `package_id` | anonymous book ID (A) or bank ID (B); `DEMO-` exactly when `demo` |
| `demo` | `true` for a synthetic package; never use one with participants |
| `builder` | `{name: "av-sound", version}` |
| `renderer_version`, `composition_contract` | provenance (`0.1.0`, `1.0.0`) |
| `profile` | A only: `P1`, `P2` or `P3` |
| `book` | A only: `frozen_head` (chain head of the book's `freeze` record, the value `freeze()` returns), `snapshot_sha256`, and the `renderer_hash` and `validator_hash` the book records ([`store.md`](../../sound/docs/store.md)) |
| `bank` | B only: `format`, `format_version`, `bank_sha256` of the bank input (section 9: qualified #26 manifest or provisional DEMO input) |
| `files` | path -> `{sha256, bytes}` for every file except `manifest.json` |
| `package_sha256` | the package hash |

**Package hash.** `package_sha256` = lowercase hex SHA-256 of the compact canonical JSON of
the manifest object without its `package_sha256` field: keys sorted by code point at every
level, separators `,` and `:`, no whitespace, ASCII. In Python:

```python
body = {k: v for k, v in manifest.items() if k != "package_sha256"}
text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
package_sha256 = hashlib.sha256(text.encode("ascii")).hexdigest()
```

The manifest holds only ASCII strings that need no escaping, integers, booleans and
objects, so another implementation can rebuild these bytes from the parsed object. The
file `manifest.json` itself is written with 2-space indentation and is not hashed as
bytes. Test vector: the committed example manifest
([`manifest.json`](../../sound/examples/package-demo/manifest.json)) and its
`package_sha256`.

## 4. Loading a package (#64)

`av_sound.package.load_package` is the reference loader. The app should do the same
checks, in this order, before a session, and block the session on any failure:

| Step | Check | Code (Python) | App fault |
| --- | --- | --- | --- |
| 1 | `manifest.json` exists, is JSON, matches the schema | `E_MANIFEST`, `E_JSON`, `E_SCHEMA` | `HASH_MISMATCH` |
| 2 | Recomputed package hash equals `package_sha256` and the value in the run sheet | `E_PACKAGE_HASH` | `HASH_MISMATCH` |
| 3 | The directory holds exactly the listed files (no missing, no extra file, no link) | `E_FILE_MISSING`, `E_FILE_EXTRA` | `HASH_MISMATCH` |
| 4 | Every file has the listed size and SHA-256 | `E_HASH_MISMATCH` | `HASH_MISMATCH` |
| 5 | Every WAV is canonical: 1 channel, 48,000 Hz, 16-bit, 44-byte header | `E_WAV_FORMAT` | `HASH_MISMATCH` |
| 6 | The documents agree with the manifest, the fixed matrix and each other | `E_CONTENT`, slot codes | `HASH_MISMATCH` |
| 7 | Every message's composite hash and length, recomputed from the atom WAVs | `E_COMPOSITE` | `HASH_MISMATCH` |

Step 7 builds no held-out buffer: the composite hash is computed incrementally
(composition.md §3). A file changed after loading must be detected again before
playback (`LoadedPackage.read_file` re-hashes; the app should hash each preloaded buffer).

## 5. `audio.json`: what to play and what to expect

Format `av-sound/package-audio` version 1.

**Study A.** `profile`; `atoms` (16, canonical order): `atom_id`, `path`, `n_samples`,
`pcm_sha256` (samples only), `file_sha256` (whole file, equals the manifest);
`messages` (32, canonical order: family, action index, referent index): `message_id`,
`action_atom`, `referent_atom`, `status` (`trained` or `heldout`), `n_samples`,
`duration_ms`, `composite_sha256`, `path` and `file_sha256` (`null` for held-out
messages).

**Study B.** `profiles` (`P1`, `P2`, `P3`); `waves`: the atoms introduced at V1 (8), V2 (4)
and V3 (4) in canonical order (menu order comes from `permutation.json`
`wave_atom_order`); `options` (192, ordered by profile, atom, rank): `profile`,
`atom_id`, `rank`, `menu` (`shown` or `reserve`), `path`, `n_samples`, `pcm_sha256`,
`file_sha256`; `messages` (32): `message_id`, `action_atom`, `referent_atom`, `status`,
`combinations` (48 = 3 profiles x 4 action ranks x 4 referent ranks): `profile`,
`action_rank`, `referent_rank`, `n_samples`, `duration_ms`, `composite_sha256`.

For every message, `composite_sha256` = SHA-256(pcm(action) + 19,200 zero bytes +
pcm(referent)) and `n_samples` = n(action) + 9,600 + n(referent) (composition.md §2-3).

- **Trained message, A:** play the WAV from disk. Its sample hash equals
  `composite_sha256` (#64 acceptance: the in-app composer must reproduce it).
- **Trained message, B:** compose in memory from the participant's committed options
  (the selected rank of each atom under the selected profile) and check the result
  against the matching combination before the slot starts.
- **Held-out message (A and B), novel block only:** compose in memory just before its
  slot, check `composite_sha256`, play once, log the hash. The playback method is the
  open #64 decision (composition.md §6). Never write the buffer to disk.

## 6. `answers.json`: hidden answers (#67)

Format `av-sound/package-answers` version 1, `hidden_answer: true`. Hidden-answer
material for the trusted task queue only: never on a participant or operator display.

| Field | Content |
| --- | --- |
| `atoms` | 16: `atom_id`, `family`, `role`, `index`, `semantic_label`, `wave` (V1: a1, a2, r1, r2; V2: a3, r3; V3: a4, r4) |
| `messages` | 32: `message_id`, `family`, `action_atom`, `referent_atom`, `action_index`, `referent_index`, `semantic_action`, `semantic_referent`, `status`, `training_wave` (trained) or `heldout_set` (held out) |
| `trained_message_ids` | the 18 trained IDs |
| `heldout_message_ids` | the 14 held-out IDs |
| `heldout_sets` | `H-V1`, `H-V2`, `H-V3` (2 IDs each), `H-W1`, `H-W4` (4 each): abstract sets of the fixed matrix |

Statuses always equal the fixed matrix (`av_sound.grammar.MATRIX`, Protocol constants);
the labels are the unit's permutation. Which set is tested at which visit depends on the
H-W1/H-W4 swap and is in `allocation.json`.

## 7. Reserved slots and `seal()`

The permutation (#29) is package-safe and can be added before allocation; schedules (#30)
and allocation facts are restricted and are added after allocation. `seal()` adds any of
them, checks them against the package, writes them and recomputes the package hash.

| Slot | Source | Checks |
| --- | --- | --- |
| `permutation.json` | `<unit>/permutation.json` of #29 (format 2), copied byte for byte | study and unit kind; labels and atom labels equal `answers.json`; the trained/held-out cells equal the fixed matrix (`E_MATRIX`); atom waves; B wave orders; **`demo: true` refused unless the package is a DEMO package** (`E_DEMO`) |
| `allocation.json` | `allocation_extras`: `swap_w1_w4` (A and B), `structured_family` (B, optional) | closed key set (no method, designer, role or seed); `novel_by_visit` derived from the swap |
| `schedules/<person_id>/<visit>.json` | `<unit>/schedules/` of #30 (format 1) | study, unit, person and visit match the path; `permutation_json_sha256` equals this package's `permutation.json`; `swap_w1_w4` equals `allocation.json`; held-out messages only in novel items due at that visit and never in `dictionary_messages`; every intended tuple equals `answers.json`; lesson `presentation` matches the study (A: structured) or `structured_family`; DEMO schedules refused for participant packages |

`allocation.json` (format `av-sound/package-allocation` version 1): `study`,
`package_id`, `swap_w1_w4`, `novel_by_visit` (A: `D0`, `D7`; B: `V1`, `V2`, `V3`, `W1`,
`W4`; held-out IDs in canonical order), optional `structured_family`. Study A tests H-W1
at D0 and H-W4 at D7, swapped when `swap_w1_w4`; H-V1..H-V3 are unused in A. Study B
tests H-V1, H-V2, H-V3 at V1, V2, V3 and H-W1, H-W4 at W1, W4, swapped when `swap_w1_w4`.

Each slot is filled once: identical bytes again change nothing, different bytes raise
`E_SLOT`. All inputs are checked before anything is written, and the sealed package is
verified with `load_package` and `scan_package`. The loader repeats every slot check.

## 8. Leak scan

`scan_package(dir, forbidden_strings=())` scans every file in the directory, listed or
not. Build and seal fail on any finding.

| Code | Finding |
| --- | --- |
| `E_HELDOUT_AUDIO` | a file, or a WAV's samples, hashes to a held-out composite hash; or a file contains a held-out message (action + 19,200 zero bytes + referent) at any byte offset |
| `E_MESSAGE_AUDIO` | a WAV longer than a motif that is not a listed trained-message WAV holding exactly its composite; any message audio in a Study B package |
| `E_WAV_FORMAT` | a non-canonical WAV (metadata chunks could carry text) |
| `E_METHOD_LABEL`, `E_DESIGNER_ID` | a token `A1`, `A2`, `A3` or `D1`, `D2`, `D3` (case-sensitive; `a1` is a matrix index, `D0` and `D7` are visits) |
| `E_METHOD_WORD` | a token containing hand, human, design, optim, evolution, genetic, transformer, llm, gpt, model, method, rating, rater, candidate or provenance |
| `E_SOURCE_KEY` | a JSON key `source` (store provenance) |
| `E_FORBIDDEN_STRING` | one of `forbidden_strings` (the builder passes the book's or bank's `source` values; strings under 6 characters are skipped) |
| `E_UNREADABLE` | a file that is neither a canonical WAV nor UTF-8 text, or unreadable atom audio |

## 9. Study B input: qualified #26 bank or PROVISIONAL DEMO bank

The manifest's `bank` record names one of two inputs; sections 2-8 are the same for both.

**Qualified #26 bank** (`format` = `av-banks/bank-manifest`, `format_version` = 1,
`bank_sha256` = the #26 bank hash, `av_generation.bank_manifest.bank_sha256` of the whole
[bank manifest](../../generation/schema/bank-manifest.schema.json)).
`av_banks.handoff.qualified_dyad_bank(bank_dir, expected_bank_sha256=...)` runs
`verify_bank`, compares the bank hash with the independently provisioned pin, refuses an
incomplete bank and a bank with reserve-rule amendments (the package and the #70 menu do
not apply `effective_menu` yet), and converts it with `to_dyad_bank`. That `DyadBank`
carries `handoff` (format, version, bank hash) and every option's `file_sha256`; the
builder re-renders each option as below and also refuses a written WAV whose file hash
differs from the bank's. The package ID is the bank ID. #70 menus verify the package
against the bank manifest itself (Unity `QualifiedBank`, [selection menus](../unity/selection-menus.md)).

**Provisional DEMO bank** (`av-sound/provisional-bank` version 1,
[`sound/schema/provisional-bank.schema.json`](../../sound/schema/provisional-bank.schema.json),
`av_sound.dyad_bank`): `bank_id`, `demo`, `labels` (atom ID -> semantic label, the dyad's
permutation) and 48 `cells` (profile, atom) with 4 options each: `rank`, `recipe`,
`pcm_sha256` and an optional opaque `source`. `bank_sha256` is the canonical hash of that
document. It stays explicitly provisional: engineering fixtures only, never a participant
handoff.

For both, the builder re-renders every option, checks the hash, refuses overflow, short
events and duplicate waveforms within a profile, and never copies `source`.

## 10. Package-hash mapping for the run sheets (#32)

The run-sheet generator pre-fills `hash_check` from a mapping of expected package hashes
([`schedules/schema/package-hashes.schema.json`](../../schedules/schema/package-hashes.schema.json),
on the schedules stack): `{format: "av-schedules/package-hashes", format_version: 1,
study, set, demo, placeholder: false, packages: {key: package_sha256}}`.
`package_hashes(packages, *, set_name, keys=None)` builds it from built (or sealed)
packages after verifying each with `load_package`; `write_package_hashes(doc, path)`
writes it as `<set>-package-hashes.json` next to the package manifests (study mappings
only outside the repository); `sound/tools/package_hashes.py` is the command line.

- Study A keys are the book IDs of the learner-facing slot list (#31, `BK-C-7QX4MN`):
  the package ID by default, or `keys={package_id: book_id}`. Learners of one book share
  the hash, as they share the book ID.
- Study B keys are dyad slot IDs (`B-C01`, spares `B-S01`): the `unit_id` of the sealed
  `permutation.json` by default; both members share the dyad package.
- One study and one DEMO status per mapping; key prefixes must match the set (`P` pilot,
  `C`/`S` confirmatory) and a sealed permutation's `set`; keys are unique. The run-sheet
  generator checks coverage of every main unit.

## 11. Python API

```python
from av_sound.package import (
    build_package, build_dyad_package, seal, load_package, scan_package,
    novel_by_visit, permutation_matrix, package_sha256,
    PackageError, PackageIntegrityError, PackageProblem, PackageResult,
    LoadedPackage, LeakReport, LeakFinding,
)
from av_sound.dyad_bank import DyadBank, BankOption, load_dyad_bank, synthetic_dyad_bank

build_package(store: VocabularyStore, book_id: str, out_dir, *,
              expected_head: str | None = None, rerender: bool = True) -> PackageResult
build_dyad_package(bank: DyadBank, out_dir) -> PackageResult
seal(package_dir, *, permutation: bytes | PathLike | None = None,
     schedules: Mapping[str, bytes | PathLike] | PathLike | None = None,
     allocation_extras: Mapping[str, Any] | None = None) -> str     # new package hash
load_package(package_dir, *, expected_package_sha256: str | None = None,
             check_composites: bool = True) -> LoadedPackage
scan_package(package_dir, *, forbidden_strings: Iterable[str] = ()) -> LeakReport
package_hashes(packages, *, set_name: str, keys: Mapping[str, str] | None = None) -> dict
write_package_hashes(doc, path) -> str                              # SHA-256 of the file
```

- `build_package` needs a frozen, non-void store book of kind `study` or `synthetic`
  with 16 labelled atoms that passes `store.verify`. Every store read (`book`,
  `records`, `verify`, `list`, `snapshot_hashes`) is anchored with `expected_head` on
  the frozen head (the chain head of the `freeze` record); a given `expected_head` (the
  head `freeze()` returned, recorded at release) must equal it. Rejected commit attempts
  logged after the freeze do not change the package; a book voided later (`void`
  event) is refused, also when it is voided during the build. `out_dir` must not exist
  or be empty. It writes into a temporary directory next to `out_dir`, verifies and
  scans it, then renames it.
- `PackageResult`: `path`, `package_sha256`, `manifest`, `leak_report`.
- `LoadedPackage`: `path`, `study`, `package_id`, `demo`, `package_sha256`, `manifest`,
  `answers`, `audio`, `permutation`, `allocation`, `schedules`, `files`,
  `combinations_checked` (A: 32; B: 1,536); `read_file(rel)` and `pcm(rel)` re-check the
  hash.
- `LeakReport`: `ok`, `findings`, `heldout_audio`, `method_strings`, `heldout_hashes`
  (A: 14; B: 672), `files_scanned`, `codes`, `to_dict()`.
- Errors: `PackageError` (`.code`, `.problems`, `.codes`); `PackageIntegrityError` from
  `load_package` (`.code` = `E_INTEGRITY`, `.problems` lists every finding).

## 12. Examples and evidence

- [`sound/examples/package-demo/`](../../sound/examples/package-demo/): the JSON files of
  the sealed synthetic package `DEMO-BOOK-P1` (manifest, answers, audio, the DEMO
  permutation A-C01 and allocation). Its WAVs are not committed:
  `uv run --project sound python sound/tools/build_example_package.py --out DIR --dyad`
  rebuilds the complete package (and a synthetic dyad package) with WAVs, and CI uploads
  them as the `package-demo` artifact, with the two run-sheet mappings. `--check` fails
  if the committed JSON differs from a rebuild. The manifest records the book's frozen
  head and code hashes, so a code change that moves `renderer_hash` or
  `validator_code_hash()` (even byte-neutral) needs `--write` in the same pull request.
- The atoms of `DEMO-BOOK-P1` use the same recipes as `DEMO-P1` in the composition test
  vectors, so every composite hash in its `audio.json` equals
  [`composition/vectors.json`](../../sound/testvectors/composition/vectors.json).
- `tests/sound/test_package.py`: three A books (one per profile), one dyad, the slot
  checks, the loader refusals and the leak-scan positive controls.
