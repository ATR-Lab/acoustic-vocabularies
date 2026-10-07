# Demo bridge protocol (version 1)

The macOS demo app (`AVSoundDemo`) drives the real Python sound engine (`av_sound`)
through a bridge process:

```
uv run --frozen --project <repo>/sound python <repo>/sound/demo/macos/bridge/av_sound_bridge.py
```

The bridge reads requests from stdin and writes responses to stdout. Each message is one
line of compact UTF-8 JSON terminated by `\n`. Diagnostics go to stderr only, never to
stdout. Requests are handled one at a time, in order. The bridge exits with status 0
after `shutdown` or at the end of stdin. On SIGTERM or SIGHUP it removes its temp
directory and exits with status 128 + the signal number (143 for SIGTERM). From the
first such signal on, and while it removes the directory on any exit, it ignores further
SIGTERM and SIGHUP, so a repeated signal cannot cut the removal short. The client sends
SIGTERM to the launched process only (`uv`, which forwards it to the bridge), never to
its whole process group.

This document is the contract between `bridge/av_sound_bridge.py` and the Swift
`AVSoundDemoCore` library. Change both sides together and bump `bridge_version`.

## Envelope

Request:

```json
{"id": 7, "cmd": "render", "args": {"recipe": {...}, "profile": "P2"}}
```

Success:

```json
{"id": 7, "ok": true, "result": {...}}
```

Failure (the bridge never exits because of a bad request):

```json
{"id": 7, "ok": false, "error": {"type": "RecipeError", "code": "E_DOMAIN", "message": "..."}}
```

- `id` is a positive integer chosen by the client and echoed back.
- `error.type` is the Python exception class name (`RecipeError`, `CompositionError`,
  `HeldOutMessageError`, `StoreError`, `InvalidIdentifier`, `OverwriteRejected`,
  `CommitRejected`, `BookFrozen`, `StoreIntegrityError`, `FallbackError`,
  `PackageError`, `ProtocolError`, `ValueError`, ...).
- `error.code` is the engine's reason code when one exists (`E_DOMAIN`, `E_HELDOUT`,
  `E_BOOK_MISMATCH`, `E_FROZEN`, `E_LABEL`, `E_IDENTIFIER`, `E_INTEGRITY`, ...),
  otherwise `null`.
- An unknown `cmd` gives `type: "ProtocolError"`, `code: "E_UNKNOWN_CMD"`. A line that
  is not valid JSON gives a response with `id: null` and `code: "E_BAD_REQUEST"`. A
  bridge whose engine is not its own checkout's answers every known command but
  `shutdown` with `ProtocolError` / `E_ENGINE_PATH` (see "Engine location").
- stdout carries responses only, one per request. The client ends a bridge (status
  failed, pending calls fail) that writes a stdout line longer than 64 MiB, or more than
  1,000 stdout lines that are not responses (blank lines included): such a launcher or
  bridge is broken, and its output is not buffered without bound. A response whose `id`
  the client never sent, or already got an answer for, counts as such a line too. The
  one late answer to a request that the client gave up on (a timeout, a cancelled call)
  is logged and ignored. stderr lines are diagnostics: the
  client cuts a line after 64 KiB and logs at most 200 lines per second (it counts the
  rest in one log note).

## Shared value shapes

- **Recipe:** the canonical recipe object of `sound/schema/recipe.schema.json`:
  `{"total_ms": 600, "pitches": [-3,0,4], "rhythm_weights": [2,1,3], "gaps_ms": [40,20], "amplitudes": [1.0,0.6,0.8]}`.
- **Profile:** `"P1"`, `"P2"` or `"P3"`.
- **Audio:** whenever a response carries audio, it has:
  - `wav_b64`: base64 of the canonical WAV file bytes (renderer spec D8);
  - `file_sha256` and `pcm_sha256`: lowercase hex.

  The client MUST check `sha256(base64decode(wav_b64)) == file_sha256` before playing
  (the same rule as the Unity loader). The three fields are `null` only in a `render`
  result whose motif overflowed (`overflow: true`): no canonical WAV exists. A held-out
  message never gets a result with audio fields: `compose` refuses it with the error
  `HeldOutMessageError` / `E_HELDOUT` (no `result`), and `composite_hash` returns the
  hash only (it has no audio fields).
- **Fractions in results** (thresholds, exact features, sums) are exact strings. A float
  for display comes with the text only in these results: `hello` (`threshold_float`
  next to `threshold`), `features` (`values` next to `exact`), and `distance` and
  `nearest` (`distance`, the float `sqrt(sum_sq / 12)`; `sum_sq` itself has no float).
  The engine records carry the exact text only: the `validate` result, the
  `fallback_scan` result and the `error.details` of `CommitRejected` (`threshold` and
  `bank_threshold` have no float there; a validation's `nearest_distance` is a float
  distance with no exact text). The text form also differs by result:
  `threshold`, `bank_threshold`, `sum_sq` and the `exact` of `features` are decimals
  when the value has one (`"0.1"`, `"0.25"`) and `p/q` otherwise (`"5/6"`), but the
  `features` of a validation result are always Python's `str(Fraction)` (`"1/4"`,
  `"1/10"`, `"0"`, `"1"`). Compare fractions as numbers, not as text.
- **Threshold arguments** (`threshold` of `validate` and `store_create`) are different:
  a string with a plain non-negative decimal, such as `"0.1"` or `"0.10"`. The engine
  refuses every other text with `ValueError` (`code: null`), including `p/q` (`"1/10"`),
  a leading point (`".1"`) and exponents. A threshold that is not a string (a JSON number,
  for example) is a bad request (`E_BAD_REQUEST`).
- **Atom reference** (an element of `committed` and similar lists):
  `{"ref_id": "K-a1", "recipe": {...}}`. The bridge renders the recipe itself to get the
  waveform hash; the client never sends audio.

## Commands

| `cmd` | `args` | `result` |
| --- | --- | --- |
| `hello` | `{}` | `bridge_version` (1), `renderer_version`, `renderer_hash`, `renderer_recipe_schema_hash`, `validator_version`, `validator_hash`, `python`, `numpy`, `sample_rate`, `gap_samples`, `threshold`, `threshold_float`, `profiles` (`[{"id","f0_hz"}]`), `domain` (`{"total_ms":[...],"pitches":[...],"rhythm_weights":[...],"gaps_ms":[...],"amplitudes":[...]}`), `reason_codes` (list), `feature_names` (list of 12) |
| `self_test` | `{}` | `ok` (bool), `message` |
| `render` | `recipe`, `profile` | `recipe` (canonical), `recipe_sha256`, `profile`, `n_samples`, `duration_ms`, `event_samples` [3], `event_onsets` [3], `gap_samples` [2], `peak`, `peak_dbfs`, `rms`, `short_event`, `overflow`, `nonfinite`, `renderer_version`, audio fields |
| `random_recipe` | `seed` (int), `admissible_only` (bool, default true) | `recipe`. Deterministic: the same seed gives the same recipe. With `admissible_only`, every event is at least 2,880 samples. |
| `features` | `recipe` | `names` [12], `exact` [12 strings], `values` [12 floats] |
| `distance` | `a`, `b` (recipes) | `distance` (float), `sum_sq` (string), `separated` (bool at the configured threshold) |
| `validate` | `candidate` (recipe object OR raw JSON text string), `profile`, `committed` ([atom references], optional), `threshold` (decimal string, optional), `use_reserved` (bool, default true) | the engine's `ValidationResult.to_dict()` (includes `ok`, `codes`, `messages`, `features`, `nearest_id`, `nearest_distance`, `pcm_sha256`, ...). Rejection is a normal result (`ok: false`), not an error. |
| `nearest` | `candidate` (recipe), `committed` ([atom references]), `profile` | `null` or `{"ref_id","index","distance","sum_sq"}` |
| `grammar` | `{}` | `families`, `atom_ids` [16], `messages` (32 × `{"message_id","family","action","referent","status","training_wave","heldout_set","is_heldout"}`) |
| `compose` | `action`, `referent` (atom references with `ref_id` = atom ID such as `K-a1`), `profile`, `book_id` (optional) | `message_id`, `n_samples`, `duration_s`, `action_samples`, `referent_samples`, `referent_onset`, audio fields. A held-out message gives the error `HeldOutMessageError` / `E_HELDOUT`, with no audio. |
| `composite_hash` | as `compose` | `message_id`, `composite_sha256`, `n_samples`, `duration_s`, `is_heldout` (allowed for held-out messages; never returns audio) |
| `synthetic_book` | `profile` | `book_id` (`DEMO-Pn`), `atoms` (16 × `{"atom_id","recipe","pcm_sha256","n_samples"}`) |
| `nonlexical_list` | `{}` | `assets` (`[{"id","kind","profile","n_samples","duration_ms","peak_dbfs","rms_dbfs","active_rms_dbfs","pcm_sha256","file_sha256","description"}]`) |
| `nonlexical_get` | `id` | the asset fields + audio fields |
| `vectors_check` | `{}` | `renderer` and `composition`, each `{"checked","mismatches":[...]}`; `ok`. Recomputed for every request. |
| `golden_check` | `{}` | `items`, `mismatches` (list), `ok`, `digest`. Recomputed for every request. |
| `store_reset` | `{}` | `root` (a fresh temp directory outside any git work tree) |
| `store_create` | `book_id` (must start `DEMO-`), `profile`, `threshold` (decimal string, optional) | `book_id`, `chain_head` |
| `store_commit` | `book_id`, `atom_id`, `semantic_label` (string or null; required, so send `null` explicitly), `recipe` | `entry` (`{"atom_id","commit_index","pcm_sha256","file_sha256","recipe"}`), `chain_head`, `outcome` (`"commit"` or `"recommit_noop"`). An overwrite attempt gives the error `OverwriteRejected`; an inadmissible recipe gives `CommitRejected` with `error.details` = the validation result. |
| `store_list` | `book_id` | `entries` (list as above), `chain_head`, `frozen`, `void` |
| `store_records` | `book_id` | `records` (the parsed log records, in order, of a book that passes the integrity checks; see Details) |
| `store_freeze` | `book_id` | `chain_head` |
| `store_verify` | `book_id`, `expected_head` (optional) | `ok`, `issues` (`[{"code","line","message"}]`; `line` is an integer or `null`) |
| `store_tamper` | `book_id`, `kind` (`"flip_blob_byte"`, `"edit_log_line"` or `"truncate_log"`) | `done` (string: what was damaged and how it is detected). Demo only: it damages the temp store so `store_verify` can show detection (the other `store_*` commands then refuse the book, except after some log cuts; see Details). A repeated tamper never repairs earlier damage; `flip_blob_byte` is refused (`ValueError`) when every committed blob of the book is already damaged, and `edit_log_line` when every record of the log is already edited (or damaged otherwise). It refuses any root that is not the bridge's own temp store. |
| `fallback_demo` | `profile` | `seed_label` (`DEMO-...`), `fallback_bank_hash`, `bank` (64 × `{"index","recipe","pcm_sha256"}`), `book` (16 × `{"atom_id","recipe","pcm_sha256"}`) |
| `fallback_scan` | `profile`, `book` ([atom references]), `used` (list of integer bank indices, optional) | the engine's `ScanResult.to_dict()` |
| `package_demo` | `{}` | `package_sha256`, `files` (`[{"path","sha256","bytes"}]`), `counts` (`{"atom_wavs","message_wavs","heldout_ids"}`), `loader_ok`, `leak_report` (dict), `answers_preview` (first 5 answers), `dir` (temp path). Every request builds, seals, loads and scans a new package in a new `dir` and then removes the previous one. |
| `shutdown` | `{}` | `{}`, then the bridge exits 0 |

## Details

These points complete the table above. Both sides follow them in version 1.

- **Bad requests:** a line that is not a JSON object, or has no positive integer `id`,
  gets `id: null` and `ProtocolError` / `E_BAD_REQUEST`. A missing or wrongly typed
  argument gets `E_BAD_REQUEST` with the request's `id`. The client gives an `id: null`
  error to its oldest pending request.
- **Argument types:** every recipe argument (`recipe`, `a`, `b`, the `candidate` of
  `nearest`, and the `recipe` of an atom reference) must be a JSON object, and every
  element of `used` an integer; anything else is `E_BAD_REQUEST`. The engine checks the
  contents: a recipe object with missing, extra or wrongly typed fields gives
  `RecipeError` / `E_SCHEMA`, values outside the domain `E_DOMAIN`, a `store_commit`
  recipe that the store refuses `CommitRejected`, and a `used` index outside the bank
  `ValueError`. The `candidate` of `validate` is the exception: the validator gives every
  JSON value a verdict, so a value that is neither an object nor a string is the normal
  rejection `E_SCHEMA` (`ok: false` in the result), not an error.
- **Numbers:** the bridge never sends `NaN` or infinities. A level that has no finite
  value (for example `peak_dbfs` of silence) is `null`.
- **Engine location:** the bridge serves only the engine of its own checkout: `av_sound`
  must be imported from `sound/src/av_sound` next to the bridge (the editable install
  that `uv sync --project sound` makes). When it comes from elsewhere, for example
  through a `PYTHONPATH` that points at another checkout (its entries come before the
  environment's site-packages), every known command but `shutdown` gets
  `ProtocolError` / `E_ENGINE_PATH`, whose message names both folders; `hello` too, so a
  client never becomes ready. The app also launches the bridge without the Python
  variables of its own environment that could change what is imported or run
  (`PYTHONPATH`, `PYTHONHOME`, `PYTHONSTARTUP`, `PYTHONUSERBASE`, `PYTHONINSPECT`,
  `PYTHONEXECUTABLE`, `PYTHONPLATLIBDIR`, `PYTHONSAFEPATH`, and `PYTHONOPTIMIZE`, which
  would strip the engine's `assert` checks), and with `PYTHONNOUSERSITE=1`. The bridge
  itself also starts under `python -O` and `-OO`.
- **Book IDs:** a book ID that does not start with `DEMO-` gives `StoreError` / `E_POLICY`.
  This applies to every `store_*` command and to the `book_id` of `compose` and
  `composite_hash`.
- **`compose`, `composite_hash`:** an atom reference may also carry a `book_id`, which
  overrides the top-level one for that atom. Atoms of different books give
  `CompositionError` / `E_BOOK_MISMATCH`. Both atoms are rendered at the one `profile` of
  the request.
- **`vectors_check`, `golden_check`:** each element of `mismatches` is a string
  `"<item>: <field>: expected X, got Y"`. In `golden_check`, `items` is a count and
  `digest` is the digest that this machine computes (it equals the manifest's when `ok`).
  Both read their files and recompute on every request; nothing is cached, so asking
  again really checks again. The same holds for `package_demo`; a failed build keeps the
  previous package.
- **`store_commit` labels:** in a `DEMO-` book every atom needs a `semantic_label` from
  the store's ontology for its family and role, each label at most once per book
  (`sound/docs/store.md`). The store checks the form of the label first: a string that
  is not an upper-case ontology identifier (for example `"add_one"`, `"ADD-ONE"` or `""`)
  gives `InvalidIdentifier` / `E_IDENTIFIER`. Then `null`, a well-formed label outside
  the ontology group of the atom's family and role (for example `"SCAN"` for `K-a1`), or
  a label already bound in the book gives `StoreError` / `E_LABEL`. The key itself is
  required: a request without `semantic_label` is a bad request (`E_BAD_REQUEST`), not
  a `null` label.
- **Chain head after a refused commit:** the store logs `OverwriteRejected` and
  `BookFrozen` attempts, so the chain head changes, but the error does not carry it. The
  client reads the new head with `store_list`.
- **Damaged books:** `store_list`, `store_records`, `store_commit` and `store_freeze`
  first check the book's log and blobs, and refuse a book that fails the integrity
  checks with `StoreIntegrityError` / `E_INTEGRITY`. `store_verify` is the command that
  reports the damage, issue by issue. So the `records` of `store_records` are the parsed
  records of a log that passed the checks, never the damaged lines. After
  `store_tamper` with `flip_blob_byte` or `edit_log_line`, every one of these commands
  refuses the book, also after the same tamper is repeated: a repeated tamper never
  repairs the damage. `edit_log_line` sets the `timestamp` of one record to
  `2000-01-01T00:00:00.000Z`: the line stays canonical JSON, but its `record_sha256` no
  longer matches (`E_RECORD_HASH`) and the next line's `prev_sha256` no longer names it
  (`E_CHAIN`); an edited freeze record no longer matches the book's `FROZEN` marker
  either (`E_MARKER`). It edits the last commit record not edited yet, and with no such
  commit left the last other record not edited yet. It never edits a record twice, and
  never touches a line that is not the canonical JSON of a record (damaged some other
  way), since a second edit could undo the first: when no record is left, it is refused
  with `ValueError` and changes nothing. Its `done` text names the codes that
  `store_verify` then reports.
  `flip_blob_byte` flips one bit in the samples of the book's first
  committed blob (in log order) that is still intact. It never flips a damaged blob
  again, since a second flip of the same bit would repair it: a repeated request damages
  the next intact blob of the book, and when none is left it is refused with
  `ValueError` and changes nothing. Blobs are content-addressed and shared by the whole
  store (`blobs/<pcm_sha256>.wav`), so a flip damages every book of the store that holds
  the same waveform. The `done` text names those other books, and they then refuse a
  flip of that blob too. A write of the same waveform does repair a damaged blob (the
  store's own rule, `sound/docs/store.md`, "Blobs"): a later `store_commit` of that
  waveform to an intact book of the store (another book, or a new one; a damaged book
  refuses the commit) moves the damaged file to `blobs/quarantine/` and writes the
  correct bytes. Every book that holds that blob then passes the checks again, unless
  it has other damage. The demo app's books never share a waveform (one book per
  profile), so the app never repairs a blob this way. `truncate_log` removes the last
  line of the log, a whole record.
  What an intact book shows after that cut depends on the record that was removed:
  - The freeze record (the last line right after `store_freeze`): the book's `FROZEN`
    marker remains, but no record matches it. These commands refuse the book
    (`E_INTEGRITY`), and `store_verify` reports `E_MARKER` (`line` `null`) even
    without `expected_head`.
  - The only record (`create_book`): the log is empty. These commands refuse the book,
    and `store_verify` reports `E_EVENT` (`line` `null`).
  - Any other record, for example a commit of an open book or a refused commit logged
    after the freeze: the shorter log is still self-consistent. These commands and
    `store_verify` without `expected_head` accept it. Only `store_verify` with an
    `expected_head` from before the cut reports it (`E_ANCHOR`).

  The `done` text says which of these the cut left.
- **`store_verify`:** `line` is the record number (`seq`) in the book's log, or `null`
  when the issue is not tied to one line: for example `E_LOG_MISSING` (no log),
  `E_ANCHOR` (`expected_head` is not in the log), `E_EVENT` for an empty log and
  `E_MARKER` for a `FROZEN` marker whose record was removed.

## Safety rules

- The bridge writes only under a temp directory it creates. Its stores, packages and
  outputs never go inside a git work tree. It removes the directory when it exits,
  including on SIGTERM or SIGHUP (`--keep-temp` keeps it). It writes no Python bytecode
  caches (`__pycache__`): run as a script it sets `sys.dont_write_bytecode`, and the app
  also sets `PYTHONDONTWRITEBYTECODE=1`.
- The launcher is not the bridge: `uv run --frozen --project <repo>/sound` creates or
  syncs the engine's virtual environment `sound/.venv` (ignored by git) from
  `sound/uv.lock` and uses uv's own cache outside the repository. That is the only write
  inside the work tree. `--frozen` makes uv use the lock file as it is: uv never
  re-resolves or rewrites `sound/uv.lock` (a tracked file), even when `pyproject.toml`
  or the user's uv settings differ from it.
- Every recipe the app sends is user-entered or synthetic. The bridge refuses non-`DEMO-`
  book IDs, never loads private seeds and never reads restricted storage.
- The demo never composes a held-out message. `compose` returns the engine's refusal.
  The bridge checks this by message ID only: it takes the recipes of each request as
  they come (also under a `DEMO-` `book_id`, whose own recipes it does not compare), so
  a trained message ID with a held-out message's recipes gets that held-out message's
  audio. The app adds a check on its side: it never
  sends `compose` for a trained message whose composite would equal that of a held-out
  message of its scratch book (for example when two slots hold the same recipe), of the
  two fixed books of the profile that the app shows (the synthetic `DEMO` book and the
  `DEMO` fallback book), or of any earlier state of a scratch book in this session. The
  app records the held-out messages of every state its scratch books take, with their
  recipes, whether or not Messages showed that state, so no later edit turns one of them
  into audio under a trained ID. These records last until the app quits. One exception:
  a fixed book's own trained message (the trained message with the two recipes it has
  in that fixed book) is not checked against earlier states. Its audio is that book's
  trained message, which the engine defines whatever the session did (`package_demo`
  writes the `DEMO` book's trained messages as WAVs), so an earlier state whose held-out
  message had the same audio did not make it; the held-out messages of the current book
  and of the fixed books are still checked. The app first compares the
  `composite_hash` of the trained message with those hashes (hashes only, no audio; the
  fixed books come from `synthetic_book` and `fallback_demo`) and refuses the message
  when one is equal.
