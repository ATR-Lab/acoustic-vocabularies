# Vocabulary store

Store format version **1** (`record_version`). Producer: vocabulary store (#11,
`av_sound.store`). Consumers: round orchestrator (#20), fallback books (#15),
Study B banks (#26), package builder (#13), golden tests (#12), reconciliation
(#33) and the integrity suite (#78).

The store keeps every committed atom of a book: its meaning, recipe, profile,
waveform and hashes. Entries can be added, never changed. There is no update and
no delete. The persistence rule is:

> `L[t+1]` restricted to the atoms of `L[t]` equals `L[t]`.

Study B grows a book 8 -> 12 -> 16 atoms. Old recipes, waveform bytes, profile and
semantic bindings must stay identical, and old entries are hashed before and after
every expansion (Study B protocol §4; Common procedures §2, engineering-pilot
check 2). Each stored waveform is the actual rendered waveform, and it is hashed
(Study A protocol §3.2).

## 1. Layout

```text
<store root>/
  blobs/<pcm_sha256>.wav        canonical WAV (renderer spec D8); written once; read-only
  books/<book_id>/log.jsonl     append-only event log of one book; read-only between appends
```

- **Blobs** are content addressed by `pcm_sha256` (renderer spec D9). Two books
  with the same waveform share one blob. A blob is written to `<name>.partial`,
  synced, then published with a hard link. The hard link is atomic and never
  replaces an existing file. On a file system without hard links, the store uses an
  exclusive create. If the blob already exists, its bytes must be identical, else the
  commit fails with `StoreIntegrityError`. Blobs are made read-only (on Windows, the
  read-only attribute).
- **Logs** have one record per line. A commit writes the blob first, then appends
  the log line, so an interrupted commit leaves at most an unreferenced blob.
- One process writes a store at a time.

## 2. Log records

Each line is the compact canonical JSON of one record, followed by one LF:
sorted keys, separators `,` and `:`, ASCII only (`ensure_ascii`), no `NaN`. The
format is [`../schema/store-record.schema.json`](../schema/store-record.schema.json).
Hex digests are lowercase.

Every record has these fields:

| Field | Value |
| --- | --- |
| `record_version` | `1` |
| `seq` | Line number, from 0 |
| `prev_sha256` | SHA-256 of the previous line's bytes without its LF; 64 zeros for line 0 |
| `record_sha256` | SHA-256 of the canonical JSON of this record without `record_sha256` |
| `event` | One of the six events below |
| `book_id` | The book (equals the directory name) |
| `timestamp` | UTC time of the append, `YYYY-MM-DDTHH:MM:SS.mmmZ`, from the store's clock |

The **chain head** of a book is the SHA-256 of its last line (without the LF).
`commit`, `create_book` and `freeze` return it.

| Event | Written when | Other fields |
| --- | --- | --- |
| `create_book` | `create_book()` (line 0 only) | `profile`, `kind`, `threshold`, `renderer_version`, `validator_version` |
| `commit` | A new atom passes all checks | `atom_id`, `family`, `role`, `matrix_index`, `semantic_label`, `recipe` (canonical), `recipe_sha256`, `profile`, `pcm_sha256`, `file_sha256`, `n_samples`, `renderer_version`, `validator_version`, `threshold`, `reserved_sha256`, `extra_references_sha256`, `source`, `commit_index` |
| `recommit_noop` | The same recipe, profile, waveform and meaning for a committed atom | `atom_id`, `original_seq`, `recipe_sha256`, `pcm_sha256`, `profile`, `semantic_label`, `source` |
| `overwrite_rejected` | Anything different for a committed atom | `atom_id`, `original_seq`, `reasons`, `attempted_recipe`, `attempted_recipe_sha256`, `attempted_profile`, `attempted_pcm_sha256`, `attempted_semantic_label`, `source` |
| `commit_rejected_frozen` | Any commit to a frozen book | `atom_id`, `freeze_seq`, `attempted_recipe_sha256`, `source` |
| `freeze` | `freeze()` | `n_entries`, `snapshot_sha256` |

Field notes:

- `kind`: `study` (a Study A book or a Study B dyad book), `fallback` (the frozen
  fallback-book namespace, #15) or `synthetic` (a fixture). Synthetic books, and
  only they, have IDs that start with `DEMO-`.
- `threshold` is exact text (`"0.1"`). A book keeps the threshold, renderer version
  and validator version it was created with. A commit with another renderer or
  validator version fails (`E_VERSION`); start a new book instead.
- `semantic_label`: the meaning bound to the atom. It must be an ontology label of
  the atom's family and role, and each label is bound to one atom per book (the
  stored permutation, #29). The labels are: K actions `ADD_ONE`, `REMOVE_ONE`,
  `FLIP_CARD`, `ALIGN_ARROW`; K referents `A` to `D`; Q actions `SCAN`, `TAG`,
  `CLOSE`, `QUARANTINE`; Q referents `E` to `H`. Fallback-book entries have `null`:
  the meaning comes from the batch permutation when an entry is committed to an
  assigned book.
- `commit_index`: 0-based commit order in the book. It is the atom index of the
  nearest-reference tie rule (Study A protocol §3.3, `nearest_reference`).
- `source`: opaque provenance (generated slot ID, fallback bank index or Study B
  option ID). It is not learner facing. The package builder (#13) must not copy it.
- `reserved_sha256`: SHA-256 of the compact canonical JSON list of the reserved
  entries used by the validator (`ReservedEntry.to_dict()`, in registry order).
- `extra_references_sha256`: SHA-256 of the compact canonical JSON list
  `[[ref_id, recipe_sha256, pcm_sha256], ...]` of extra references, or `null`.
- `reasons`: what differs from the committed entry, in the order `profile`,
  `recipe`, `semantic_label`, `waveform`. An attempt with another profile also
  differs in `waveform` unless the caller asserted the committed hash. An attempted
  recipe that does not parse is `null` and differs in `recipe` and `waveform`.
- `snapshot_sha256`: `snapshot_digest(snapshot_hashes())`, the SHA-256 of the compact
  canonical JSON object `{atom_id: pcm_sha256}`.

Example: line 0 of the synthetic book `DEMO-P1` built by the growth demo
(section 5). Its SHA-256, the chain head after `create_book`, is
`1a982f08e2d987f6cc8b346d52daeeac66cde790b4721bc2af183f0c66507966` (`create_head` in
[`../testvectors/store/growth.json`](../testvectors/store/growth.json)).

```json
{"book_id":"DEMO-P1","event":"create_book","kind":"synthetic","prev_sha256":"0000000000000000000000000000000000000000000000000000000000000000","profile":"P1","record_sha256":"8f4cf2d251d32e88907c3c6291c167078f689718047c1f0b7db72d92ab2355a9","record_version":1,"renderer_version":"0.1.0","seq":0,"threshold":"0.1","timestamp":"2026-01-01T00:00:00.000Z","validator_version":"0.1.0"}
```

## 3. Operations

The signatures are in
[`docs/interfaces/sound-engine.md`](../../docs/interfaces/sound-engine.md#vocabulary-store-11).

`commit(book_id, atom_id, semantic_label, recipe, *, source, ...)` checks in this
order:

1. Arguments: book ID, atom ID (`K-a1` .. `Q-r4`), label and source format
   (`InvalidIdentifier`). The book must exist (`NotFound`) and pass the integrity
   checks of section 4 (`StoreIntegrityError`). Nothing is logged.
2. **Frozen book**: log `commit_rejected_frozen`, raise `BookFrozen`.
3. **Committed atom**: compare recipe, profile, waveform and meaning. If all are
   identical, log `recommit_noop` and return the existing entry. Otherwise log
   `overwrite_rejected` and raise `OverwriteRejected`. The entry and its blob stay
   byte-identical.
4. **New atom**: versions (`E_VERSION`), the optional profile assertion
   (`E_PROFILE`) and the label (`E_LABEL`). Then `validate()` runs against the
   book's committed entries in commit order (same profile, both families and roles),
   plus any extra `references`, with the book's threshold and the reserved signals. A
   rejection raises `CommitRejected(result)`. A waveform that differs from the
   optional `pcm_sha256` assertion raises `E_WAVEFORM`. None of these is logged:
   the caller archives rejected candidates (#20, #24).
5. Write the blob, append the `commit` record, return `(StoreEntry, chain_head)`.

`freeze(book_id)` appends a `freeze` record; a frozen book accepts no commit. A
second `freeze` changes nothing. `get`, `list`, `snapshot_hashes`, `snapshot`,
`records`, `book` and `head` read the book. Each read and each write first runs
the checks of section 4 (without re-rendering) and refuses a damaged book.

`StoreEntry` has `atom_id`, `profile` and `pcm`, so it satisfies the composer's
`AtomAudioLike`: pass entries to `compose_message` and `composite_hash` directly.
`entry.reference()` is the validator `Reference` with `ref_id` = atom ID.

## 4. Verification

`verify(book_id, *, rerender=True, expected_head=None) -> VerifyReport` never raises
for integrity problems. It reports every problem it finds:

| Code | Problem |
| --- | --- |
| `E_LOG_MISSING` | No log file |
| `E_LOG_TORN` | The last line has no LF (truncated or interrupted write), or an empty line |
| `E_LOG_JSON` | A line is not strict ASCII JSON, or not an object |
| `E_LOG_NONCANONICAL` | A line differs from the canonical JSON of its record (for example CRLF) |
| `E_LOG_SCHEMA` | A record does not match `store-record.schema.json` |
| `E_RECORD_HASH` | `record_sha256` does not match the record |
| `E_SEQ` | `seq` is not the line number |
| `E_CHAIN` | `prev_sha256` is not the hash of the previous line |
| `E_BOOK_ID` | A record names another book |
| `E_EVENT` | Impossible event order (an empty log, line 0 not `create_book`, a commit after `freeze`, a second commit of one atom) |
| `E_RECORD` | Inconsistent fields (atom parts, commit index, versions, threshold, label, recipe hash, no-op or overwrite details, freeze snapshot) |
| `E_BLOB_MISSING`, `E_BLOB_FORMAT`, `E_BLOB_HASH`, `E_BLOB_FILE_HASH` | A blob is missing, not a canonical WAV, does not hash to its name, or does not match `file_sha256` |
| `E_RERENDER` | Re-rendering the recipe gives another waveform (`rerender=True`) |
| `E_ADMISSIBILITY` | An entry is not admissible against the earlier entries (duplicates, separation, short events) with the book threshold (`rerender=True`; reserved signals are not rechecked) |
| `E_ANCHOR` | `expected_head` is not the hash of any line of the log |

What this detects:

- A change to **one byte** of any line: the line's `record_sha256` no longer
  matches, and the next line's `prev_sha256` no longer matches. This includes the
  last line, which no later line covers.
- A change to one byte of any blob: the samples no longer hash to the file name,
  or the header is not canonical.
- A deleted, inserted, duplicated or reordered line in the middle: `E_SEQ`,
  `E_CHAIN`.
- A truncated line: `E_LOG_TORN`.
- **Whole lines removed from the end** leave a valid shorter log. Only an anchor
  detects this: pass a chain head that was recorded outside the store as
  `expected_head`. The orchestrator (#20) records the head returned by each commit
  in its batch log. The release records the head of each frozen book.
- Someone who rewrites the whole log and recomputes every hash is also detected
  only by an anchor, or by `E_RERENDER` and `E_ADMISSIBILITY` if a recipe changed.

Anchors make the hash chain useful: record chain heads where the store operator
cannot change them.

## 5. Growth and persistence checks

- `snapshot_hashes(book_id) -> {atom_id: pcm_sha256}` in commit order.
- `snapshot(book_id) -> {atom_id: {recipe_sha256, pcm_sha256, profile, semantic_label}}`:
  everything that must not change.
- `persistence_violations(before, after)` lists atoms of `before` that are missing
  or different in `after`. It is empty exactly when the persistence rule holds.
- `snapshot_digest(snapshot)` is one hash over a whole snapshot.

[`../tools/store_growth_demo.py`](../tools/store_growth_demo.py) grows the three
synthetic books 8 -> 12 -> 16 in the Study B wave order (wave 1: a1, a2, r1, r2 of
each family; wave 2: a3, r3; wave 3: a4, r4). It prints the before and after hash
tables, attempts an overwrite of each kind, freezes the book and verifies it. With a
fixed clock and no reserved signals, the log bytes are identical on every platform:
`--write` stores the chain heads and snapshots in
[`../testvectors/store/growth.json`](../testvectors/store/growth.json), and
`tests/sound/test_store.py` recomputes them on Linux, macOS and Windows.

```bash
uv run --project sound python sound/tools/store_growth_demo.py          # report
uv run --project sound python sound/tools/store_growth_demo.py --check  # vectors
```

A change to the record format, to canonical JSON or to rendered bytes changes these
vectors. Regenerate them with `--write` in the same pull request and say why.

## 6. Storage policy

**Pending (human): the restricted storage location for confirmatory books is agreed
with the study coordinator role.** The rules below apply wherever it is.

- **Study books** (pilot and confirmatory Study A books, Study B dyad books and the
  fallback namespace) live in **restricted storage outside this repository**. They are
  never added to git and never to Git LFS. `create_book` refuses a `study` or
  `fallback` book whose store root is inside this repository's working tree
  (`E_POLICY`).
- **Public records** of a study book may contain only its **chain head** and its
  **snapshot digest** (`snapshot_digest`). Do not publish per-atom hashes, recipes
  or WAV files of a study book before the study ends. The recipe domain has
  4 x 13^3 x 4^3 x 3^2 x 3^3 = 136,687,104 recipes per profile, so one atom's
  `pcm_sha256` can be inverted by rendering every recipe. A digest over a whole book
  cannot.
- **Fixtures** are synthetic `DEMO-` books, built in temporary directories by tests
  and tools. Only their JSON hash manifests are committed
  ([`../testvectors/store/`](../testvectors/store/)).
- **Book IDs** are anonymous. `check_book_id` accepts 3 to 64 ASCII letters, digits
  and inner hyphens. It refuses Windows device names, IDs that differ from an
  existing book only in letter case, and IDs with a token `A1`, `A2` or `A3` or a
  method word (hand, human, design, optim, evolution, genetic, transformer, llm,
  gpt, model, method). This is a cheap guard, not proof of anonymity: generate book
  IDs without method information (#20). Method labels stay in the allocation key,
  outside the store.
- **Repository guard** (`tools/repo_guard.py`): `*.wav` files that are not LFS
  pointers fail the guard, and LFS uploads are disabled, so a store directory cannot
  be committed by accident. The guard also fails on path components such as
  `private`, `local-data`, `raw-results`, `recordings`, `codebooks` and `.local`.
  Keep store roots outside the working tree; inside it they are blocked.
- Back up the restricted storage with its read-only attributes. A restore must be
  checked with `verify(..., expected_head=<recorded head>)`.

## 7. Recovery

Never edit a log line or a blob. Corrections go into the deviation log (Common
procedures §8).

- **Interrupted append** (`E_LOG_TORN` on the last line only): the bytes after the
  last LF were never a complete record and no hash covers them. Record a deviation,
  remove only those bytes, then run `verify` with the last recorded anchor.
- **Leftover `.partial` file** in `blobs/`: it is never referenced and is replaced
  by the next write of the same blob.
- **Any other integrity problem**: the book refuses reads and writes. Keep it
  unchanged for the audit and follow the deviation procedure. Do not repair it.

## 8. Platform notes

- Files are written in binary mode, so line endings never change on Windows. The
  `.gitattributes` rule for `sound/testvectors/**` keeps the vectors byte-exact.
- Read-only files: clear the attribute (`os.chmod(path, stat.S_IWRITE)`) before you
  delete a store, for example in test clean-up. The tests do this.
- Book IDs are directory names. The ID rules keep them valid and distinct on
  Windows, macOS and Linux.
