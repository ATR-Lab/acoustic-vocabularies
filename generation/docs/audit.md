# Generation audit reports (#24)

Module `av_generation.audit`. It builds per-book tables and a batch summary of one
Study A batch run from the run's logs, a masked and an unmasked version together, and
set tables over the complete batches of a study set for the analysis pipeline (#34) and
the pilot review (O6.2.1). Requirements: Study A protocol §3.3 (effort logged
separately), §3.7 (per-slot rejection, per-atom and whole-book fallback, wall time,
human effort) and §8 (generation effort, invalid-slot and fallback rates as secondary
endpoints); analysis plan §3 (labelled nonfallback sensitivity).

## Command line

From the repository root (`<run_dir>` is a run directory, `rundir.RunLayout`):

```bash
# One batch: <run_dir>/audit/unmasked/ and <run_dir>/audit/masked/
uv run --project generation python -m av_generation.audit batch <run_dir> \
    [--out DIR] [--machines machines.json] [--strict]

# A study set: masked table for the blinded analysis inputs, unmasked table for the keys
uv run --project generation python -m av_generation.audit set --study A --set pilot \
    --masked-out DIR --unmasked-out DIR <run_dir> [<run_dir> ...]

# Tally sheet for the hand-count check (restricted, like the logs)
uv run --project generation python -m av_generation.audit tally <run_dir> --out tally.csv
```

`batch` prints the files written with their SHA-256 and `ok`; every problem (and note)
goes to stderr; `--strict` exits 1 when a check found a problem (notes do not count).
`tally` exits 1 when a count differs. Every refusal exits 2 with `error (<code>): ...` on
stderr and no traceback: an `AuditError` (`E_INPUT`, also for an unreadable `--machines`
file; `E_STUDY`, `E_MASKING`, `E_SET`, `E_SCHEMA`), the restricted-run policy
(`E_POLICY`) or another file that cannot be read or written (`E_IO`). Python API:
`build_audit(run_dir, out_dir, *, machines=None) -> AuditResult(files, ok, problems,
notes)`, `build_set_audit(run_dirs, out_dir, *, study, set_name, masked) ->
SetAuditResult`, `tally_sheet(run_dir, path) -> bool`, and the pieces `read_run_logs`,
`compute_audit`, `report_texts`.

There is no `generation audit` console script: adding one needs a `[project.scripts]`
entry in `generation/pyproject.toml`, which implementers do not edit (architecture §14).
`python -m av_generation.audit` is the same command.

## Inputs (and what is never read)

`read_run_logs` opens exactly these files of the run directory and records the SHA-256
of each in `summary.json` `sources`:

| File | Used for |
| --- | --- |
| `run-manifest.json` | run ID and kind, closed or not, method map cross-check |
| `config.json` (`BatchConfig`) | books, methods, designer IDs, profile, atom order, panel seats |
| `logs/slots.jsonl` | outcomes, recipes, effort fields of the 576 proposal slots |
| `logs/slot-refusals.jsonl` | refusal count (optional file) |
| `logs/ratings.jsonl` | submitted ratings and rater time per slot |
| `logs/decisions.jsonl` | eligibility, scores, round-4 incumbent and action |
| `logs/commits.jsonl` | commit sources, final and voided store books, committed recipes |
| `logs/fallback-scans.jsonl` | bank scans per atom (optional file) |
| `logs/timing.jsonl` | wall, startup, familiarization and operator time |

Every record is validated against its schema; a torn last line or a malformed record
stops the audit (`AuditError`, `E_INPUT`; repair torn tails with
`jsonio.repair_torn_tail` first). Learner data, plays, LLM request logs and stores are
never opened (`test_only_the_run_files_are_read`). Machine specifications come from an
optional `--machines` JSON file, `{role: {field: text}}`, copied from the apparatus
manifest (for example `{"llm_host": {"gpu": ..., "driver": ...}, "a1_station": {...}}`);
they appear in the unmasked summary only.

## Outputs

Under `<out>/unmasked/` (restricted: names methods) and `<out>/masked/` (anonymous book
IDs only); rows are sorted by book ID, never by method.

| File | Rows | Columns |
| --- | --- | --- |
| `books.csv` | one per book | `BOOK_COLUMNS` / `MASKED_BOOK_COLUMNS` (the #34 contract) |
| `book-<book_id>-atoms.csv` | 16, in generation order | `ATOM_COLUMNS` / `MASKED_ATOM_COLUMNS` |
| `book-<book_id>-slots.csv` | 192, by atom position, round, slot | `SLOT_COLUMNS` / `MASKED_SLOT_COLUMNS` |
| `summary.json` | the batch | `audit-summary.schema.json` |
| `summary.md` | the batch, for people | books, outcomes per code and round, atoms, wall time, effort, diversity and durations, checks, sources |

CSV: header row, `\n` line ends, booleans `0`/`1`, empty cell = not applicable,
diversity with 6 decimals. Times are run-clock milliseconds.

### `books.csv` columns

| Column | Definition |
| --- | --- |
| `batch_id`, `book_id`, `profile` | from the batch config |
| `method`, `designer_id` | from the batch config (unmasked only) |
| `slots_total` | slot records of the book (192 in a complete batch; a substituted book keeps generating, so also 192) |
| `slots_valid`, `slots_invalid` | slots with outcome `valid`, and all others |
| `n_<code>` | slots per outcome code, in `outcomes.OUTCOME_CODES` order; they sum to `slots_total` (unmasked only) |
| `n_llm_server_error` | slots with `llm_status` `server_error` (counted inside `n_invalid_json`; unmasked only) |
| `atoms_committed` | atoms committed to the book's final store book (the store book of its last commit) |
| `atoms_selector`, `atoms_bank_fallback`, `atoms_book_fallback` | those atoms by commit source |
| `failed_generation` | the final store book holds the fallback book (whole-book substitution) |
| `nonfallback` | all 16 atoms came from the selector: no bank atom, no fallback book (analysis plan §3 sensitivity) |
| `wall_ms` | batch generation wall time: the sum of the 16 atom durations (`atom_start` to `atom_end`; pauses inside an atom count, breaks between atoms do not; an atom interrupted by a restart counts both parts, see `summary.json`); the same for the three books, which run in parallel; empty when an atom has no pair |
| `startup_ms` | startup intervals attributed to the book: events naming the book, or the method's component (`a1`; `a2`; `llm`/`a3`) (unmasked only) |
| `operator_ms` | `duration_ms` of `operator_action` events naming the book (unmasked only) |
| `rater_ms` | rater person-time on the book's candidates: the sum over its rating records (every seat, placeholders included) of `t_ms - slot_start_ms` |
| `design_active_ms` | A1: sum of the slots' `design_ms` (unmasked only) |
| `familiarization_ms` | A1: paired `familiarization_*` events naming the book or its designer (unmasked only) |
| `model_runtime_ms`, `tokens_in`, `tokens_out` | A3: sums of `latency_ms` and token counts over the slots with a model call (`llm_status` set, not `overflow_input`) (unmasked only) |
| `candidate_diversity` | mean pairwise 12-feature distance among the valid candidates of each atom, averaged over the atoms with two or more |
| `committed_diversity` | mean pairwise 12-feature distance among the committed atoms (120 pairs) |
| `total_ms_450` .. `total_ms_900` | committed atoms per `total_ms` value |
| `message_ms_min`, `message_ms_max` | shortest and longest of the 32 complete messages (action + 200 ms + referent), from metadata only |
| `message_duration_violations` | messages outside 1,100-2,000 ms; each one is also a problem |

The 12-feature distance is `av_sound.features` (exact sums, one correctly rounded
conversion, floats added in a fixed order), so diversity is the same on every platform.

### Per-book tables

`book-<id>-atoms.csv`: outcome counts of the atom's 12 slots, `valid_r1`..`valid_r4`,
`n_eligible` (eligible candidates over the atom's four rounds: the sum of its slot rows'
`eligible`, since each decision lists only its own round's candidates; empty without
decisions), `selected_slot_id` (the round-4 incumbent) and `round4_action` (`commit`,
`fallback_scan`, `archive`, `archive_none`), `fallback_scan` (a bank scan was logged),
`source` with `source_slot_id` (selector) or `bank_index` (bank), `voided_source` (an
earlier commit in a store book voided by the substitution), the committed `total_ms`,
the atom's candidate diversity, rater time and (unmasked) per-method effort.

`book-<id>-slots.csv`: one row per proposal slot with its slot ID, position, round and
slot, `valid`, `total_ms` and `recipe_sha256` of its recipe, `n_ratings` (submitted
ratings), `rater_ms`, `eligible` and `score` (latest decision listing it), `selected`
(round-4 incumbent) and `committed`; unmasked also `outcome`, `llm_status`,
`validator_codes`, `latency_ms`, `design_ms` and tokens.

Traceability: every count in `books.csv` is the sum of the matching column over the
book's slot rows or atom rows, and every atom row names its committed slot or bank
index (`test_every_number_traces_back_to_slot_ids`). Timing columns trace to the
timing events listed in `summary.json` `timing`.

### `summary.json`

`audit-summary.schema.json`: `books` (the `books.csv` rows), `timing` (batch wall time,
per appointment, per atom with its four round durations and the longest round, the
longest atom and appointment, all startup and all operator time), `checks` (`complete`,
`ok`, `problems`, `notes`, `slot_refusals`, `fallback_scans`), `machines` (unmasked
only) and `sources`.

Durations pair start and end events with the same key in log order (`pair_intervals`):
the end event's `duration_ms` when set, else the difference of `t_ms` on one run clock,
so no span is measured across processes. An end before its start, an end without a
start or a start without an end is a problem and is not counted.

Restarts. A new orchestrator process starts a new run clock (`t_ms` from 0) and logs
`resume` when it takes over the run (#20 `Orchestrator.resume`); events of one process
share the clock origin `wall_utc - t_ms` (without `wall_utc`, a clock that went back
marks the restart). The atom, round or appointment in progress when the clock restarts
is interrupted, not a problem: its time until the last event logged on its clock before
the restart is kept and listed in `checks.notes`.

- The orchestrator logs `atom_start` (`resumed`) and the round's `round_start` again
  (and `appointment_start` when the appointment has atoms left): the interval continues
  from the `resume` event, and both parts count.
- An appointment whose last atom the resume finished gets no `appointment_end`: only
  its part before the restart counts (the atom's own wall time has both parts), and the
  note says so.
- An older open interval (later intervals of the same kind started before the restart)
  is not interrupted: its end is missing, which stays a problem.

The time between the last event before the restart and the crash is not in the logs, so
an interrupted interval is a lower bound.

## Masking

- Masked tables drop `METHOD_COLUMNS`, `METHOD_ATOM_COLUMNS` and `METHOD_SLOT_COLUMNS`:
  method and designer, every outcome count and code, LLM status, startup, operator and
  per-method effort, latencies and tokens. Invalid slots' `total_ms` and `recipe_sha256`
  are blank in the masked slot table (whether an invalid slot has a recipe depends on
  the method).
- `summary.json` with `masked: true` must not hold those columns or machine
  specifications (the schema refuses them).
- Every masked text is checked with `masking.masking_findings` before anything is
  written; a finding (for example a run ID containing `A3`) stops the audit with
  `E_MASKING`.
- Problems and notes have a masked text and an unmasked one. Most name books, atoms,
  slots and rating slots only (the masked tables show those anyway) and are the same in
  both. A text that would name or reveal a method gets a method-neutral masked version:
  unpaired startup and familiarization events (their keys hold the component, the book
  and the designer) read `startup timing: ...` / `familiarization timing: ...`, and a
  practice slot (only A1 has practice mode) reads `slot <id>: not a slot of batch <id>`.
  The unmasked report keeps the detail; both list the same number of problems.
- The unmasked directory is for the generation operator. Store it where session
  experimenters and blinded analysts cannot reach it; hand out `masked/` only. Reports
  of pilot, confirmatory and practice runs cannot be written inside a git work tree
  (`E_POLICY`).
- Masking removes labels, not every inference: outcome patterns and diversity can still
  hint at a method.

## Checks

The audit never stops on a count problem; it reports it (sorted, deduplicated) in
`checks.problems`, `summary.md` and `AuditResult.problems` (unmasked texts):

- slot records per book (192) and per atom (12), duplicates, slots of another batch,
  practice slots, slots whose book, run or profile disagrees with the config;
- rating records per book (192 x seats), ratings of unknown slots;
- decision records per book (64), duplicate decisions;
- commits: one per atom in the final store book (16), a selector commit that does not
  match a valid slot of its atom (recipe hash), a bank commit without an index, commits
  to a voided store book without a substitution, substitution and `book_substituted`
  events that disagree;
- message durations outside 1,100-2,000 ms;
- missing or unpaired timing events;
- missing required logs, and run-manifest books that differ from the config.

Notes (`checks.notes`, `AuditResult.notes`) are not problems: `ok` and `--strict` ignore
them. Today they list the intervals interrupted by a restart (see `summary.json`).

`checks.complete` is false while the run manifest is open or after a
`batch_incomplete` event.

## Set tables and the cross-batch summary

`build_set_audit` reads every given run, leaves out incomplete runs (listed in
`excluded_runs`), and needs exactly one complete run per batch (`E_SET` otherwise; also
for a run of another set and for study B). It writes `{study}-{set}-audit.csv` (masked)
or `{study}-{set}-audit-unmasked.csv` with one row per book sorted by batch and book, and
`{study}-{set}-audit.md` / `-audit-unmasked.md`: per method (unmasked: books, slots,
invalid %, server errors, selector and bank atoms, failed and nonfallback books, effort)
and outcome codes per method, then per book and per batch. The analysis pipeline reads
the masked table at `inputs/generation/{study}-{set}-audit.csv`; the unmasked table's
proposed analysis path is `keys/generation/{study}-{set}-audit-unmasked.csv`.

## Hand-tally check

`tally_sheet(run_dir, path)` writes, per book and quantity (slots, each outcome code,
server errors, committed atoms by source, failed generation, `total_ms` counts), the
count from the raw JSONL lines (`independent_counts`: standard library only, no record
classes), the audit's count, whether they match, and an empty `hand_count` column. For
the dry-run batch (#22) a person fills `hand_count` from the logs and attaches the sheet
to the PR; every row must match exactly.

## Synthetic fixture and DEMO summary

`av_generation._audit_synth.write_synthetic_batch(runs_root, spec)` writes a synthetic
batch's logs with the shared record types (no component runs; it is not the #22 dry
run): a bank fallback, a whole-book substitution with continued generation, an
`archive_none` atom, a resume between appointments that restarts the run clock,
per-method outcome mixes, missing ratings and one input overflow. Every timing event
carries `wall_utc` (the process clock's start + `t_ms`). `SynthSpec.crash_at` (off by
default) adds a mid-atom crash and resume. `generation/runs/DEMO-AUDIT-01/` holds its
audit (`books.csv`, `summary.json`, `summary.md`, masked and unmasked), its tally sheet
and `hashes.json` (every output and run file); the logs are not committed because they
are rebuilt bit for bit. Rebuild with:

```bash
uv run --project generation python -m av_generation._audit_synth generation/runs/DEMO-AUDIT-01
```

`tests/generation/test_audit.py` rebuilds it and compares bytes on Linux, macOS and
Windows; CI uploads the full synthetic run and report as the `generation-ci-<os>`
artifact (`generation/out/ci/audit/`).

## Pending

- Audit report and hand-tally comparison of the #22 dry-run batch: run `batch` and
  `tally` on the dry-run directory after #22, fill `hand_count` by hand.
- Pilot review (O6.2.1): run `set --set pilot` over the 3 pilot batches.
- Machine specifications: the apparatus manifest (O6.1.6) has no generation-host fields
  yet; until it does, the operator writes the `--machines` file from the hosts.
