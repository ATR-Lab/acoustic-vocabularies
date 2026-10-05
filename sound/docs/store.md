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

A book holds at most 16 atoms, one per atom ID. The ordered bank of 64 fallback
recipes per profile (#15) is **not** a book: it lives outside the store as a hashed
manifest. Only the complete 16-atom fallback book of each profile is a store book
(`kind="fallback"`).

## 1. Layout

```text
<store root>/
  blobs/<pcm_sha256>.wav          canonical WAV (renderer spec D8); written once; read-only
  blobs/quarantine/<name>.<sha>   a blob file that did not hash to its name (kept for the audit)
  books/<book_id>/log.jsonl       append-only event log of one book; read-only between appends
  books/<book_id>/FROZEN          write-once marker of the record that closed the book
  books/<book_id>/VOID            write-once marker of the void record
  books/<book_id>/.lock           lock file
```

- **Blobs** are content addressed by `pcm_sha256` (renderer spec D9). Two books
  with the same waveform share one blob. A blob is written to a private
  `<name>.<pid>-<thread>.partial` file, synced, then published atomically: with a
  hard link, which never replaces a file, or, on a file system without hard links,
  with a rename. A reader never sees a partial blob under its final name. A blob's
  bytes are a function of its name, so a file under that name with other bytes
  (for example from a crash with an older writer, or tampering) cannot belong to an
  intact book: the next write of that blob moves it to `blobs/quarantine/` and
  publishes the correct bytes. Blobs are read-only (on Windows, the read-only
  attribute).
- **Logs** have one record per line. A commit writes the blob first, then appends
  the log line, so an interrupted commit leaves at most an unreferenced blob. If an
  append fails in the process (for example a full disk), the log is truncated back
  to its previous size.
- **Markers.** `freeze()` appends the `freeze` record, then writes `FROZEN`;
  `void()` appends the `void` record, then writes `VOID` (and `FROZEN` if the book
  was open). A marker is one line of canonical JSON
  `{"book_id", "event", "line_sha256", "seq"}` that names its record. Removing the
  record from the log no longer reopens the book: the marker no longer matches
  (`E_MARKER`).
- **Locking.** Every operation on a book holds an OS lock on `books/<book_id>/.lock`
  (`flock` on POSIX, `msvcrt.locking` on Windows) from the first read to the last
  write. Threads and processes on one host are serialized. The OS releases the lock
  when a process dies, so a crash leaves no stale lock; the file itself is harmless.
  Before each append the store also checks that the log still has the size it read
  (`E_CONCURRENT`). Network file systems may not honour these locks: keep one
  writing host per store. A waiting call raises `StoreLocked` after `lock_timeout`
  seconds (default 60).
- **Lookups are exact.** Book IDs match the directory name exactly, letter case
  included, on every platform (`NotFound` otherwise). `create_book` refuses an ID
  that differs from an existing book only in case.

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
| `event` | One of the eight events below |
| `book_id` | The book (equals the directory name) |
| `timestamp` | UTC time of the append, `YYYY-MM-DDTHH:MM:SS.mmmZ`, from the store's clock |

The **chain head** of a book is the SHA-256 of its last line (without the LF).
`create_book`, `commit`, `freeze`, `void` and `recover_torn_tail` return it.

| Event | Written when | Other fields |
| --- | --- | --- |
| `create_book` | `create_book()` (line 0 only) | `profile`, `kind`, `threshold`, `renderer_version`, `validator_version`, `renderer_hash`, `validator_hash` |
| `commit` | A new atom passes all checks | `atom_id`, `family`, `role`, `matrix_index`, `semantic_label`, `recipe` (canonical), `recipe_sha256`, `profile`, `pcm_sha256`, `file_sha256`, `n_samples`, `renderer_version`, `validator_version`, `threshold`, `reserved_sha256`, `extra_references_sha256`, `source`, `commit_index` |
| `recommit_noop` | The same recipe, profile, waveform and meaning for a committed atom | `atom_id`, `original_seq`, `recipe_sha256`, `pcm_sha256`, `profile`, `semantic_label`, `source` |
| `overwrite_rejected` | Anything different for a committed atom | `atom_id`, `original_seq`, `reasons`, `attempted_recipe`, `attempted_recipe_sha256`, `attempted_profile`, `attempted_pcm_sha256`, `attempted_semantic_label`, `source` |
| `commit_rejected_frozen` | Any commit to a frozen or void book | `atom_id`, `freeze_seq`, `attempted_recipe_sha256`, `source` |
| `freeze` | `freeze()` | `n_entries`, `snapshot_sha256` |
| `void` | `void()` | `cause`, `reason`, `superseded_by`, `n_entries`, `snapshot_sha256` |
| `deviation` | `recover_torn_tail()` | `deviation` (`torn_tail_removed`), `removed_bytes`, `removed_sha256`, `reason` |

Field notes:

- `kind`: `study` (a Study A book or a Study B dyad book), `fallback` (the frozen
  fallback-book namespace, #15) or `synthetic` (a fixture). Synthetic books, and
  only they, have IDs that start with `DEMO-`.
- `threshold` is exact decimal text (`"0.1"`). `create_book` refuses a threshold
  without a finite decimal form, such as 1/3.
- A book keeps the threshold, renderer and validator it was created with:
  `renderer_version`, `validator_version`, `renderer_hash` (`av_sound.renderer_hash()`)
  and `validator_hash` (`av_sound.store.validator_code_hash()`: the validator
  version, the code digests of `validate.py`, `features.py`, `recipe.py`,
  `reserved.py`, `_schemas.py` and the recipe-schema digest). A new commit under
  other code fails (`E_VERSION`); start a new book. Committed entries stay protected:
  overwrite attempts are still rejected and logged.
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
- `freeze_seq`: the line of the record that closed the book (`freeze`, or `void` for
  a book that was open).
- `cause`: `failed_generation` (whole-book fallback substitution, Study A protocol
  §3.7), `batch_rebuild` (the batch is rebuilt with a new panel, Study A protocol
  §3.3) or `other`. `reason` is free text: include the deviation ID.
  `superseded_by` names the replacing book, or is `null`.
- `snapshot_sha256`: `snapshot_digest(snapshot_hashes())`, the SHA-256 of the compact
  canonical JSON object `{atom_id: pcm_sha256}`.

Example: line 0 of the synthetic book `DEMO-P1` built by the growth demo
(section 6). Its SHA-256, the chain head after `create_book`, is
`e51009a7b56e00c3975708f8b4f1d04cd5431c49e85467ba696553989813ed13` (`create_head` in
[`../testvectors/store/growth.json`](../testvectors/store/growth.json)).

```json
{"book_id":"DEMO-P1","event":"create_book","kind":"synthetic","prev_sha256":"0000000000000000000000000000000000000000000000000000000000000000","profile":"P1","record_sha256":"13d2e1a6d5d067cd43205f6fd6f7de8d67bf565c8f12a7b78514b57fc418edbd","record_version":1,"renderer_hash":"03a99200e8546fc113d320d499a071159afa219d914b54e98c88449791fd151d","renderer_version":"0.1.0","seq":0,"threshold":"0.1","timestamp":"2026-01-01T00:00:00.000Z","validator_hash":"fd2d6acfb5aa9f673b46e9e8baab3abda816142af6ddd5e32a5bcecef928a650","validator_version":"0.1.0"}
```

## 3. Operations

The signatures are in
[`docs/interfaces/sound-engine.md`](../../docs/interfaces/sound-engine.md#vocabulary-store-11).

`commit(book_id, atom_id, semantic_label, recipe, *, source, ...)` checks in this
order, all under the book's lock:

1. Arguments: book ID, atom ID (`K-a1` .. `Q-r4`), label and source format
   (`InvalidIdentifier`). The book must exist (`NotFound`) and pass the integrity
   checks of section 5 (`StoreIntegrityError`), including `expected_head` when it is
   given. Nothing is logged.
2. **Frozen or void book**: log `commit_rejected_frozen`, raise `BookFrozen`
   (`.void` tells which).
3. **Committed atom**: compare recipe, profile, waveform and meaning. If all are
   identical, log `recommit_noop` and return the existing entry. Otherwise log
   `overwrite_rejected` and raise `OverwriteRejected`. The entry and its blob stay
   byte-identical.
4. **New atom**: versions and code hashes (`E_VERSION`), the optional profile
   assertion (`E_PROFILE`) and the label (`E_LABEL`). Then `validate()` runs against
   the book's committed entries in commit order (same profile, both families and
   roles), plus any extra `references`, with the book's threshold and the reserved
   signals. A rejection raises `CommitRejected(result)`. A waveform that differs from
   the optional `pcm_sha256` assertion raises `E_WAVEFORM`. None of these is logged:
   the caller archives rejected candidates (#20, #24).
5. Write the blob, append the `commit` record, return `(StoreEntry, chain_head)`.

`freeze(book_id)` appends a `freeze` record and writes `FROZEN`; a frozen book
accepts no commit. A second `freeze` appends nothing. If a freeze or void was
interrupted after its record but before its marker, the book reports `E_MARKER`
and refuses commits until `freeze()` is called again, which only completes the
missing marker.

`void(book_id, *, cause, reason, superseded_by=None)` marks a whole book as
replaced (whole-book fallback substitution, batch rebuild). Nothing is deleted. A
void book accepts no commit, `book().void` is true and `books(void=False)` leaves
it out. A second `void` raises `StoreError(E_VOID)`.

`get`, `list`, `snapshot_hashes`, `snapshot`, `records`, `book` and `head` read the
book. Each read and each write first runs the checks of section 5 (without
re-rendering) and refuses a damaged book. `get`, `list`, `snapshot_hashes`,
`snapshot`, `records`, `book`, `commit`, `freeze` and `void` take an optional
`expected_head=`.

`StoreEntry` has `atom_id`, `profile` and `pcm`, so it satisfies the composer's
`AtomAudioLike`: pass entries to `compose_message` and `composite_hash` directly.
`entry.reference()` is the validator `Reference` with `ref_id` = atom ID.

## 4. Anchors (mandatory for consumers)

The hash chain proves that a log is internally consistent. It cannot prove that
nobody removed lines from the end or rewrote the whole log with fresh hashes and
markers (for example swapped two meanings). Only a chain head recorded **outside
the store**, where the store operator cannot change it, can prove that. So:

| Consumer | Records | Passes as `expected_head` |
| --- | --- | --- |
| Round orchestrator (#20) | The head returned by every `create_book`, `commit`, `freeze` and `void`, in its batch log | The last recorded head, to every `commit` and `freeze` of that book |
| Study B bank and selection (#26, #70) | The head after each wave's commits | The previous wave's head, to the next wave's commits and to `snapshot()` |
| Package builder (#13) | The head of each frozen book in the package manifest | The head from the release record, to `list()` or `verify()` before building |
| Reconciliation (#33) and integrity suite (#78) | - | The heads recorded by #20 and #13, to `verify()` and `snapshot()` |
| Release and G4 | The head and `snapshot_digest` of every frozen book (public) | - |

An anchor passes when it is still one of the log's line hashes, so later appends
are allowed. A missing anchor fails every read and write with `StoreIntegrityError`
(`E_ANCHOR`), and `verify` reports it.

## 5. Verification

`verify(book_id, *, rerender=True, expected_head=None) -> VerifyReport` never raises
for integrity problems. It reports every problem it finds:

| Code | Problem |
| --- | --- |
| `E_LOG_MISSING` | No log file (or no book with exactly this ID) |
| `E_LOG_TORN` | The last line has no LF (truncated or interrupted write), or an empty line |
| `E_LOG_JSON` | A line is not strict ASCII JSON, or not an object |
| `E_LOG_NONCANONICAL` | A line differs from the canonical JSON of its record (for example CRLF) |
| `E_LOG_SCHEMA` | A record does not match `store-record.schema.json` |
| `E_RECORD_HASH` | `record_sha256` does not match the record |
| `E_SEQ` | `seq` is not the line number |
| `E_CHAIN` | `prev_sha256` is not the hash of the previous line |
| `E_BOOK_ID` | A record names another book |
| `E_EVENT` | Impossible event order (an empty log, line 0 not `create_book`, a commit after `freeze` or `void`, a second commit of one atom, a second `freeze` or `void`) |
| `E_RECORD` | Inconsistent fields (atom parts, commit index, versions, threshold, label membership and uniqueness, profile, recipe hash, no-op or overwrite details, freeze or void snapshot) |
| `E_MARKER` | A `FROZEN` or `VOID` marker without its record (lines removed), a marker that does not match its record, or a missing marker |
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
- A removed `freeze` or `void` record: `E_MARKER`.
- **Whole lines removed from the end** of an open book, and a log rewritten with
  fresh hashes and markers: only an anchor detects these (section 4), or
  `E_RERENDER` and `E_ADMISSIBILITY` if a recipe changed.

## 6. Growth and persistence checks

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
uv run --project sound python sound/tools/store_growth_demo.py --write  # regenerate
```

A change to the record format, to canonical JSON, to rendered bytes, or to the
code of the renderer or validator modules (it changes `renderer_hash` or
`validator_hash` in line 0) changes these vectors and the example in section 2.
`--write` regenerates both; do it in the same pull request and say why.

## 7. Storage policy

**Pending (human): the restricted storage location for confirmatory books is agreed
with the study coordinator role.** The rules below apply wherever it is.

- **Study books** (pilot and confirmatory Study A books, Study B dyad books and the
  fallback namespace) live in **restricted storage outside every git work tree**.
  They are never added to git and never to Git LFS. A log holds full recipes and
  meanings, which regenerate every WAV, so a log is as sensitive as the WAVs.
- `create_book` refuses a `study` or `fallback` book (`E_POLICY`) whose store root is
  inside this repository's source tree or inside **any** git work tree: it looks for
  `.git` (a directory, or a file for worktrees and submodules) in the root and all
  its parents. If a parent cannot be checked, it refuses (fail closed).
- The root `.gitignore` ignores store layouts anywhere in the repository
  (`**/books/*/log.jsonl`, `**/books/*/FROZEN`, `**/books/*/VOID`,
  `**/books/*/.lock`, `**/blobs/*.wav`, `**/blobs/quarantine/`, `*.partial`).
- **Public records** of a study book may contain only its **chain head** and its
  **snapshot digest** (`snapshot_digest`). Do not publish per-atom hashes, recipes
  or WAV files of a study book before the study ends. The recipe domain has
  4 x 13^3 x 4^3 x 3^2 x 3^3 = 136,670,976 recipes per profile, so one atom's
  `pcm_sha256` can be inverted by rendering every recipe. A digest over a whole book
  cannot.
- **Fixtures** are synthetic `DEMO-` books, built in temporary directories by tests
  and tools. Only their JSON hash manifests are committed
  ([`../testvectors/store/`](../testvectors/store/)).
- **Book IDs** are anonymous. `check_book_id` accepts 3 to 64 ASCII letters, digits
  and inner hyphens. It refuses Windows device names (CON, PRN, AUX, NUL, COM0-9,
  LPT0-9) and IDs with a token `A1`, `A2` or `A3` or a method word (hand, human,
  design, optim, evolution, genetic, transformer, llm, gpt, model, method). This is
  a cheap guard, not proof of anonymity: generate book IDs without method
  information (#20). Method labels stay in the allocation key, outside the store.
- **Repository guard**: the repository guard is pending in #89 and is not yet on
  `main`. As proposed there, it rejects `*.wav` files that are not LFS pointers and
  some path components, but it does not recognise a store log. The store pull
  request proposes a guard rule that rejects `log.jsonl` under a `books/` path
  component, `FROZEN` and `VOID` markers and any `blobs/` path component. Until
  then, the `.gitignore` rules and the work-tree check above are the protection.
- Back up the restricted storage with its read-only attributes. A restore must be
  checked with `verify(..., expected_head=<recorded head>)`.

## 8. Recovery

Never edit a log line or a blob. Corrections go into the deviation log (Common
procedures §8).

- **Interrupted append** (`E_LOG_TORN` on the last line only, for example after a
  power cut): call `recover_torn_tail(book_id, reason="<deviation ID> ...")`. It
  removes only the bytes after the last LF, which no hash covers, and appends a
  `deviation` record with their length and SHA-256. Then run `verify` with the last
  recorded anchor. It refuses a log with any other problem. A log with no complete
  line means the book was never created: record a deviation and remove the book
  directory.
- **Interrupted freeze or void** (`E_MARKER`, marker missing): call `freeze()`.
- **Leftover `.partial` files** in `blobs/`: never referenced; safe to delete.
- **Quarantined blobs** in `blobs/quarantine/`: evidence for the audit; record a
  deviation.
- **Any other integrity problem**: the book refuses reads and writes. Keep it
  unchanged for the audit and follow the deviation procedure. Do not repair it.

## 9. Platform notes

- Files are written in binary mode, so line endings never change on Windows. The
  `.gitattributes` rule for `sound/testvectors/**` keeps the vectors byte-exact.
- Read-only files: clear the attribute (`os.chmod(path, stat.S_IWRITE)`) before you
  delete a store, for example in test clean-up. The tests do this.
- A read-only copy of a store (an audit medium) can be read: without a writable
  lock file, reads proceed without the lock because nothing can write there.
- Book IDs are directory names. The ID rules keep them valid and distinct on
  Windows, macOS and Linux.
