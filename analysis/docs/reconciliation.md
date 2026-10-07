# Reconciliation (#33, O4.5.1)

Per-visit reconciliation compares what a visit was scheduled to do with what the raw logs
say happened: trial and play counts, waveform hashes, held-out exposure, store growth,
the yoked ledger, visit windows and deviation links. A clean reconciliation is needed
before a derived score is trusted and at every data lock. Reconciliation never computes
accuracy and never reads the condition key, so masked staff can run it.

Contracts shared with #34 and #35 (tables, codes, data roots, masking) are in
[`architecture.md`](architecture.md) and
[`docs/interfaces/analysis.md`](../../docs/interfaces/analysis.md). Protocol references:
Common procedures sections 6-8, Study A protocol section 8, Study B protocol sections 5,
10 and 11, analysis plan sections 2, 6 and 8.

## 1. Commands

| Command | Does | Exit codes |
| --- | --- | --- |
| `av-analysis synth-logs --demo-seed DEMO-... --out DIR [--study A\|B\|both] [--set pilot\|confirmatory] [--units N] [--max-persons N]` | builds a SYNTHETIC data root with inputs and clean raw logs | 0, 2 refused |
| `av-analysis synth-logs ... --out ROOT --fault NAME --visit ID [--documented]` | injects one fault into a synthetic visit (builds the root first if `ROOT` is not one) | 0, 2 |
| `av-analysis synth-logs --demo-seed DEMO-... --out DIR --fault-suite` | runs every fault on every visit type it applies to, writes `fault-suite.csv` and the reports | 0 all as expected, 1 otherwise |
| `av-analysis synth-logs --demo-seed DEMO-o4.5.1-example --out analysis/examples/reconciliation-demo --examples` | rewrites the committed example reports and `fault-suite.csv` | 0 |
| `av-analysis reconcile VISIT_ID... --root DIR` or `--all` | checks C1-C8, writes `reconciled/<visit_id>/reconciliation.json` | 0 every visit passes, 1 a visit fails, 2 refused input |
| `av-analysis derive --root DIR` | writes the six tables and the two area manifests | 0, 2 refused (for example a report older than its inputs) |
| `av-analysis refresh --root DIR` | operator sequence after a visit: `reconcile --all`, `derive`, `dashboard` (skeleton) | worst step code |
| `av-analysis import-export VISIT_ID --root DIR --export DIR --export-manifest-sha256 HEX --run-sheet FILE --run-sheet-sha256 HEX --deviations FILE --deviations-sha256 HEX` | verifies a station ExportBundle and the console's run-sheet and deviation exports, then creates `raw/<visit_id>/` (section 3, "Importing a station export"; #81) | 0 written, 2 refused (nothing written) |
| `av-analysis block-durations VISIT_ID --root DIR [--out FILE]` | per-block run-sheet durations against scheduled seconds and the visit against its booked minutes (#81) | 0, 2 refused |

Raw files are opened read-only. `reconcile` hashes every raw file it reads before and
after the run; the report's `raw_unchanged` is true only when nothing changed. Otherwise
C1 reports `RAW_HASH_CHANGED` naming the files (data-root paths), which no deviation
record can resolve, and the visit fails (exit 1).

## 2. Modules

| Module | Contents |
| --- | --- |
| `loaders` | template CSV loading, header rule (template columns, then extension columns in order, each optional), value domains (`value_problems`, `DOMAINS`), `load_raw_visit`, `load_deviations_log`, `raw_visit_ids`, `RefusedInputError` |
| `references` | `load_references` (schedules, generated run sheet, slot or dyad list, reveal log, package-hash mapping, package `manifest.json` and `audio.json`, Study B store snapshots and receipts), `ExpectedHash`, `committed`, `revealed_persons`, `load_reveal` |
| `reconcile` | `Report`, `reconcile_visit`, `reconcile_many`, `write_report`, the `reconcile` command |
| `reconcile_checks` | the rules of C1-C8, `Found`, `Record`, deviation links (`link`, `LINK_CATEGORIES`), `evaluate` |
| `ledger` | the per-person exposure fold (`fold`, prior counts), the `exposure-cumulative` rows (`build_ledger`) |
| `derive` | the reconciled and derived tables, area manifests, the `derive` command |
| `synthetic_inputs` | synthetic package JSON, package-hash mapping, Study B store history, reveal log |
| `synthetic_logs` | synthetic raw logs for every visit type, fault injection, the fault suite, examples, the `synth-logs` command |
| `export_import` | the column adapter (#81, #72, #73): `read_bundle` (manifest, file hashes, inventory, journal chain), `bind` (reveal-log binding), `map_trial_log`, `map_exposure_ledger`, `map_deviations`, `exit_manifest`, `write_plan` (exclusive create), the source of every template column (`TRIAL_SOURCES`, `EXPOSURE_SOURCES`, `DEVIATION_SOURCES`), the `import-export` command |
| `block_durations` | block durations against the booking that `av_schedules.run_sheet_output` renders (`booking`, `compare`, `visit_report`), the `block-durations` command and its schema |

## 3. Inputs

### Raw visit folder

`raw/<visit_id>/` holds `trial-log.csv`, `exposure-ledger.csv`, `visit-run-sheet.csv`,
`deviations.csv` (methodology template headers) and `exit-manifest.json`. The study-wide
`raw/deviations-log.csv` holds later deviations and corrections.

**Extension column `trial_ref` (proposal).** The exposure-ledger template has no column
that links a play to its scheduled opportunity. The provisional export has one
(`attempt_id`), so the exposure ledger may end with `pcm_sha256` and `trial_ref`
(`templates.EXTENSION_COLUMNS`): the trial-log `trial_id` of the attempt that requested
the play. `import-export` writes both (below). Without `trial_ref`, plays cannot be
counted per trial: C1 reports `RAW_FORMAT` and the play-count rules of C2 are skipped.

### Importing a station export (#81)

`export_import` turns one provisional station export into a raw visit folder. It runs
before reconciliation and refuses (exit 2, `ExportRefused` with a code, nothing written)
rather than guess:

1. **Verify.** The ExportBundle manifest (`data-export-provisional-1`) must have the
   SHA-256 supplied out of band; every listed file its size and SHA-256; the directory
   exactly the listed files (`EXPORT_MANIFEST_HASH`, `EXPORT_FILE_HASH`,
   `EXPORT_INVENTORY`). The journal segments must form one hash chain (each record hashed
   without its final `sha256` member, sequence from 0, `previous_sha256` links, the
   manifest identity on every record), with the manifest's record count and head hash, and
   torn tails acknowledged by a `recovery` record unless the manifest says
   `unacknowledged_torn_tail` (`EXPORT_JOURNAL_*`). Both CSVs carry the manifest identity
   and row counts. The published sample layout (`docs/data/synthetic-visit`: the original
   manifest, `public-manifest.json`, `events.jsonl`) is read too. The console run sheet and
   deviation export need their SHA-256 values (`EXPORT_FILE_HASH`).
2. **Columns.** The header contract and CSV headers must equal the provisional columns
   (`PROVISIONAL_TRIAL_COLUMNS`, `PROVISIONAL_EXPOSURE_COLUMNS`, the same as
   `apparatus/data/*.provisional.csv`), the run sheet the run-sheet template and the
   deviation export the console's six columns; anything else is `EXPORT_COLUMNS`. Lesson
   and assessment tables in a bundle have no mapping yet (`EXPORT_UNMAPPED_TABLE`).
3. **Bind.** `VISIT_ID` must be the person slot the root's reveal log binds to the export's
   `coded_id`, plus the export's visit; the run sheet must name that slot or coded ID and
   visit (`EXPORT_VISIT_MISMATCH`, `EXPORT_IDENTITY_UNBOUND`). A SYNTHETIC root takes only
   coded IDs with a synthetic marker; a REAL root refuses them, unqualified headers and
   torn tails.
4. **Map.** Each template column has one source (`TRIAL_SOURCES`, `EXPOSURE_SOURCES`,
   `DEVIATION_SOURCES`): a provisional column copied unchanged (`attempt_id` -> `trial_id`
   and `trial_ref`, `audio_request_id` -> `event_id`, `coded_id` -> `participant_id`,
   `audio_onset_estimate_mono_ms` -> ledger `audio_onset_mono_ms`, console `staff_code` ->
   `operator`, `note` -> `observed_problem`); the bound visit (`study`, `batch_id` or
   `dyad_id`, ledger `wave`); derived from the verified export (`presentation_index` from
   the attempt's ordered `audio_request_ids`; `pcm_sha256` from the journal's
   `audio_request` record, on the trial log that of the first request; deviation
   `category` from the console reason: `technical`, `procedure`, and `visit_window` or
   `pair_window` -> `window`); or none, written empty. Without a producer source today:
   trial `codebook_id`, `role`, families, `trial_type`, `message_id`, hidden answer,
   `trained_status`, prior exposure counts, `scheduled_onset_mono_ms`, offsets,
   `sim_time`, `commit_mono_ms`, scores, `feedback_shown`, `dictionary_available`,
   `actual_delay_hours`; ledger `stage`, `atom_or_message_id`, `whole_phrase`, display
   fields, `audio_offset_mono_ms`, `retrieval_opportunity`, feedback and choice; deviation
   participant, unit, event, action, exposure, endpoint, resolution, reviewer. Values are
   never repaired (for example `accepted_or_rejected` `pending`, console `yes` and
   `ops-demo`): the loaders report them as `RAW_FORMAT`.
5. **Write.** `raw/<visit_id>/` must not exist; files are created exclusively, flushed and
   read back, the exit manifest last. The original bundle (`export/bundle/`, station
   layout with `manifest.json`) and the console deviation export
   (`export/console/deviations.provisional.csv`) are kept unchanged and listed in the exit
   manifest; the run sheet is the console's bytes. The exit manifest is the projection of
   `docs/interfaces/analysis.md`; `closed` is `interrupted` with an unacknowledged torn
   tail or no `visit_complete` journal record.

Only synthetic exports have been imported (tests: `tests/analysis/test_export_import.py`).
The published samples verify as published but name visit `DEMO`, so the end-to-end test
binds a re-chained copy to a `synth-logs` person slot: `reconcile` reads the folder with
intact raw integrity and fails on content (null template columns, trials that are not the
person's schedule), as expected for that fixture.

### Analysis-side value rules (Pending confirmation by #67, #72, #73)

Values that `vocab` fixes (playback and audible status, response codes, fault codes,
times, staff IDs, comfort checks, deviation categories) are checked as listed there.
`loaders.DOMAINS` adds these rules:

| Column | Rule |
| --- | --- |
| trial log `message_id` | the cue of the trial: message ID, atom ID (`K-a1`) or speech ID (`K-ADD_ONE-B`); empty for the profile menu and no-cue trials; its family prefix equals `semantic_family` |
| trial log `participant_id`, ledger `participant_id` | the coded participant ID the reveal log binds to the slot |
| trial log `batch_id`, `dyad_id`, `codebook_id` | Study A batch, empty, book ID; Study B empty, dyad slot, bank ID |
| run sheet `participant_id` | the person slot or its coded participant ID |
| ledger `stage` | the schedule trial type of the play's opportunity, or `practice` |
| ledger `atom_or_message_id` | the item heard; the atom of a menu (all candidates of the menu); empty for the nonsemantic profile example |
| ledger `whole_phrase` | `true` exactly when the item is a complete message |
| ledger `candidate_id` | atom menus `<atom_id>-<rank>` (`K-a1-2`), profile menu the preset (`P2`) |
| ledger `accepted_or_rejected` | `accepted`, `rejected` or empty |
| ledger `active_choice_or_default` | `active_choice`, `default` or empty |
| ledger `presentation_index` | 1-based play number within its trial |
| deviations `prior_audio_exposure` | `none`, `audible`, `uncertain`; empty counts as `uncertain` |
| deviations `event_id` | the row it explains, as reports name rows (trial or event ID, `visit-run-sheet.csv:<block>`, an atom ID, a data-root path), the visit ID or the person slot; in `raw/deviations-log.csv` a row is qualified by its visit as `<visit_id>/<row>` (section 6); characters `A-Za-z0-9._:/-` |
| integers, times | decimal integers; ISO 8601 with seconds and a UTC offset |

A value outside its domain is a `RAW_FORMAT` discrepancy naming the row (trial or event
ID, else `file:line`); the loader never repairs it.

### Reference inputs

Paths are `paths.INPUT_PATHS`. Required for any check beyond C1 and C7: the person's
schedule and generated run sheet of the visit (both validated with the schedules checks),
the slot or dyad list and the reveal log (hash chain replayed with
`av_schedules.reveal.RevealLog`), the package-hash mapping and the package's
`manifest.json` and `audio.json` (its `audio.json` entry in the manifest must match).
Missing or invalid, they give one `REFERENCE_INPUT` discrepancy and C2-C6 are
`not_applicable`. The person's other schedules and the Study B store snapshot of every
visit up to this one and the receipts are also read; a missing one is a non-fatal
`REFERENCE_INPUT` (Study B ranks then come from the latest earlier snapshot).

### Refusals

`RefusedInputError` (exit 2): a REAL root with a SYNTHETIC exit manifest, an export
without `source` or with an unacknowledged torn tail, IDs with a `DEMO-` or `SYNTHETIC`
marker, DEMO lists, mappings, packages or schedules, synthetic store snapshots or
receipts; a SYNTHETIC root with a REAL exit manifest; a book key under `inputs/`.

## 4. Plays, trials and the exposure fold

`ledger.fold` processes a person's held visits in visit order and, within a visit, the
trial log in file order; each trial takes the plays whose `trial_ref` names it. A trial's
prior counts are the plays before its own first play that consumed exposure
(`confirmed_audible`, `estimated` or `uncertain`): of the trial's complete message
(`prior_phrase_exposures`) and of its component atoms played in isolation, menu
candidates included (`prior_atom_exposures`). Plays that name no logged trial count after
the visit's last trial. A held-out message's first consuming play is its first audible
exposure; a confirmed no-onset play consumes nothing.

## 5. Check rules

| Check | Rules (discrepancy codes) |
| --- | --- |
| C1 raw-integrity | exit manifest present (`RAW_MANIFEST_MISSING`) and valid for this visit (`RAW_FORMAT`); every listed file present (`RAW_FILE_MISSING`) with the listed size and SHA-256 (`RAW_HASH_CHANGED`); no unlisted file (`RAW_FILE_UNLISTED`); the four logs present (`RAW_FILE_MISSING`); every header and value valid, identity columns equal to the visit, the exit manifest and the reveal log, at least one run-sheet `start_time`, the `trial_ref` column present, each deviation ID used by one record of the visit only (`RAW_FORMAT`; study-wide log lines only when they concern this visit, section 6); reference inputs (`REFERENCE_INPUT`); no raw file read by the run changed, appeared or disappeared while it ran (`RAW_HASH_CHANGED` naming data-root paths, never resolved) |
| C2 counts | every scheduled trial has a trial-log row (`COUNT_MISSING_TRIAL`; plays that name it are listed); no unscheduled row that is not a retry (`COUNT_EXTRA_TRIAL`); a row's type and cue equal the schedule's, rows in scheduled order (longest increasing run kept, the rest reported per block) and blocks started in order (`BLOCK_ORDER`); per trial, plays of its cue equal the scheduled plays (lessons 3, menus 8, tests 1, no-cue 0) and no play of another item (`COUNT_MISSING_PLAY`, `COUNT_EXTRA_PLAY`); unlinked plays (`COUNT_EXTRA_PLAY`); run-sheet blocks and expected counts equal the generated sheet and `actual_count` equals the logged scheduled trials of the block (`COUNT_RUN_SHEET`); response events: a delivered test trial (playback `observed_complete` or `uncertain`, or a no-cue trial) has a response code, and a `commit` has its `commit_mono_ms`, unless a fault code of type `missing_response_log` is logged (`RESPONSE_EVENT_MISSING`); technical flags: a trial with playback `confirmed_no_onset` or `uncertain`, a scheduled cue never requested (`not_requested`), `frame_freeze_ms` over 250 or `reset_ok` false has a `technical_fault_code` (`TECHNICAL_FLAG_MISSING`) |
| C3 waveform-hashes | the package manifest's hash and the run sheet's `hash_check` equal the mapping (`PACKAGE_HASH_MISMATCH`); each trial's logged hash and each play's hash equals the PCM or the file hash of the item (`WAVEFORM_HASH_MISMATCH`): Study A from `audio.json`; Study B the option of the candidate (menus) or of the committed rank in the visit's snapshot (atoms) and the combination of committed ranks (messages); an empty hash only for composed audio with a `pcm_sha256` value (`WAVEFORM_HASH_MISSING`). Nonsemantic profile examples and speech commands have no package hash and are not checked (Pending #64, #71) |
| C4 exposure | no complete held-out message in any stage but `novel` (`HOLDOUT_OUTSIDE_TEST`); a held-out message's first consuming play happens at the visit whose novel block schedules it (`HOLDOUT_WRONG_VISIT`, reported at the visit where it happened); a held-out trial logged with `prior_complete_phrase_exposures` 0 after earlier consuming plays (`HOLDOUT_REPEAT_AS_NOVEL`); audible or uncertain audio with `exposure_consumed` false (`UNCERTAIN_NOT_CONSUMED`); a `playback_status` that claims more delivery or less exposure than the trial's linked plays: `observed_complete` with a play that is not `confirmed_audible` or `estimated`, `confirmed_no_onset` with a consuming play, `not_requested` with plays (`PLAYBACK_STATUS_CONFLICT`; the conservative `uncertain` is never a conflict); a retry must name a scheduled trial of the visit whose plays are all `confirmed_no_onset`, be the only retry, not retry a retry, run at the end of that trial's block (after every logged scheduled trial of the block, with only retries of the block's trials between them; Common procedures section 6) and run the same cue (`RETRY_LINK_BROKEN`); feedback or dictionary in a pre-old, protected or validity trial, or feedback content with a test play (`ANSWER_DISPLAY_LEAK`) |
| C5 growth (B) | receipt self-hashes and the `before_head`/`after_head` chain from the profile receipt on, rejected receipts not moving the head; the snapshot's self-hash (`manifest_sha256`), its `book_head` in the chain and not before the previous snapshot's; profile and profile receipt unchanged since the first snapshot; the atoms of this visit's wave present and new entries backed by a committed receipt (`STORE_CHAIN_BROKEN`); every atom committed at an earlier visit keeps its committing snapshot's entry: profile, rank, PCM and file hash, receipt (`OLD_ATOM_CHANGED`) |
| C6 yoked-ledger (B V1-V3) | evaluated once per dyad over both members' ledgers, identical in both reports: every selection play (profile and atom menus) of the second member names a selection play of the first (`YOKED_SOURCE_MISSING` when it names none, `YOKED_MISMATCH` when it names another event; a selection play of the first member naming a source is also `YOKED_MISMATCH`) and equals it in stage, item, candidate, acceptance, hash, phrase flag, presentation index, meaning display and choice flag, and within 100 ms in onset after the display start, audio duration, meaning-display duration and pause (a timing logged on one side only differs; Study B protocol sections 5.2 and 5.3) (`YOKED_MISMATCH`); every source play has exactly one copy, in the same order (`YOKED_SOURCE_MISSING`, `YOKED_MISMATCH` naming the events of both members); the second session starts after the first ended and within 24 h of its start (`YOKED_GAP`); while the other member's visit has no raw logs, `YOKED_SOURCE_MISSING` names that visit. Details are role-neutral: each text is raised for plays of either member |
| C7 windows | not applicable to anchor visits (A D0, B V1); the anchor and the preceding visit must be held with a date (`VISIT_ORDER`); `windows.classify` early or late (`WINDOW_EARLY`, `WINDOW_LATE`); the visit on a later date than the visit that must precede it (`VISIT_ORDER`) |
| C8 deviation-links | every unresolved discrepancy of C1-C7 gives `DEVIATION_MISSING` with the same rows; a trial-log `deviation_id` or ledger `matching_deviation_id` that is not in the visit's deviations or the study-wide log gives `DEVIATION_UNKNOWN` (resolved by a `correction` record naming the row) |

## 6. Deviation links

A discrepancy is resolved by the first match among the records that concern the visit,
in this order:

1. a trial-log `deviation_id` or ledger `matching_deviation_id` on a row it names, when
   that record exists;
2. a record whose `event_id` is one of its rows (a trial or event ID, a file or run-sheet
   row, an atom), any category;
3. a record whose `event_id` is the visit ID, then one whose `event_id` is the person slot,
   then one with empty `event_id` and the coded participant ID, each only with a category
   that fits the code (`reconcile_checks.LINK_CATEGORIES`, column "Deviation category" in
   the guide below).

**Records that concern a visit** (`reconcile_checks.visit_records`): its own
`deviations.csv`, and the records of the study-wide `raw/deviations-log.csv` that name no
other participant (`participant_id` empty, the person slot or the coded ID) and no other
unit (`dyad_or_batch` empty or the visit's batch or dyad slot) and whose `event_id` is:

- the visit ID or the person slot;
- `<visit_id>/<row>`, a row of the visit's report qualified by the visit, for example
  `A-P01-L01-D0/visit-run-sheet.csv:trained`, `B-P01-M1-V2/K-a1` or
  `B-P01-M1-V2/E0a1b2c3d4`;
- a row ID that starts with `<visit_id>-` (scheduled trial IDs and their retries);
- empty, with the coded participant ID.

A log record that a row of the visit names by `deviation_id` or `matching_deviation_id`
also concerns it, and explains only through that link. Row names such as
`visit-run-sheet.csv:trained`, `exit-manifest.json`, `receipts.jsonl:3`, atom IDs and
producer event IDs recur across visits, persons and units, so an unqualified one in the
study-wide log explains nothing. C6 also uses the other member's records (scoped the same
way to its visit), visit and slot. A deviation ID names one record of the visit only
(C1 `RAW_FORMAT` otherwise); `derive` looks records up per visit in the same scope, never
by ID across visits. Among equal matches the smallest deviation ID wins.
`DEVIATION_MISSING` is never resolved: write the record and rerun.

## 7. Report

`reconciled/<visit_id>/reconciliation.json` ([schema](../schema/reconciliation.schema.json)):
identity, `inputs` (every file read, with size and SHA-256), `raw_unchanged`, `checks`
(C1..C8: status, number of discrepancies, unresolved), `discrepancies` (`seq` in check
order then by rows; code, rows, deviation link, resolved, suspension event, detail) and
`summary` (`status` `pass` unless a check fails or `raw_unchanged` is false;
`discrepancies` and `unresolved` count
C1-C7 and `DEVIATION_UNKNOWN`; suspension events of all of them, resolved or not). Check
status: `pass` none, `explained` all resolved, `fail` any unresolved, `not_applicable`.
Details name rows, item IDs and rules only.

## 8. Tables (`derive`)

`derive` refuses a report (rerun `reconcile`) whose listed inputs changed since it was
written, or when an input a rerun would read has appeared since: a raw file of the
visit's folder, of the person's earlier visits or of the other member's visit (for
example the partner visit imported after the report), `raw/deviations-log.csv`, or a
reference input the report names in `REFERENCE_INPUT`. Persons are the slots bound in the
reveal logs; a visit is held when its raw folder exists. Deviation records are looked up
per visit (section 6).

| Table | Rule |
| --- | --- |
| `trials` | one row per trial-log row of a reconciled visit that is a scheduled trial or a retry linked to a logged one (extra trials are left out). Schedule fields and the private intended labels come from the schedule (a retry takes its original's). `valid_delivery`: verified playback (`observed_complete`, the scheduled number of linked plays, all `confirmed_audible` or `estimated`; always for no-cue trials), a response code for test trials, no fault type, and no hash, play-count, `PLAYBACK_STATUS_CONFLICT` or `RESPONSE_EVENT_MISSING` discrepancy on the trial or its plays. `exposure_consumed`: any linked play consumed exposure (without linked plays: `observed_complete` or `uncertain`). Prior counts and `novelty` from the fold. `deviation_ids`: the row's own known `deviation_id` and the links of resolved discrepancies naming the trial or its plays; `discrepancy_codes`: their codes. Lost opportunities: each `COUNT_MISSING_TRIAL` resolved by a `technical` or `audio` record is a `row_source` deviation row (`OPPORTUNITY_LOST`, fault type `other`, `valid_delivery` false, response fields null, `exposure_consumed` unless the record says `prior_audio_exposure` `none`) |
| `endpoints` | per person, visit and battery block of the schedule (also for visits not held): `accounted_n` = trials rows that are not retries (lost rows included), `fault_n` those with fault codes or types, `lost_n`, `valid_delivery_n` (valid, or its retry valid), `retry_n`; status `complete`, `partial`, `missing`; `missing_reason`: the visit state for visits not held (`withdrawn`, `missed`, `pending`), `pending` for a held visit not reconciled yet, `withdrawn_mid_battery` when a missing trial of the battery is resolved by a `withdrawal` or `comfort` record, `technical_stop` when one is unresolved and the exit manifest says `interrupted`, else `other`; `planned_endpoint` = complete and in window (or anchor) |
| `visit-status` | every expected visit of every revealed person; `visit_state` `held`, else `withdrawn` (a `withdrawal` record naming the slot, the participant, or this or an earlier visit), `missed` (a `missed_visit` record naming the visit), `pending`; reconciliation and failed checks from the report (`not_run` without one); timing from the first run-sheet start of the visit and of its anchor; `pair_gap_hours` (absolute hours between the members' session starts) and `pair_gap_ok` (`windows.yoked_gap_ok`) for Study B V1-V3, identical on both rows; `actual_minutes` first start to last end; `overrun` beyond booking + 10 min; opportunity and fault counts from `trials` (not retries); deviation counts from the records that concern the visit (section 6) |
| `discrepancies` | every discrepancy of every report |
| `exposure-cumulative` | per person and item (atoms and complete messages) scheduled up to the last reconciled visit or played: first consuming play, counts of consuming plays by phase (selection, teaching, test), `scheduled_novel_visit` from the schedules, C4 codes of the person's reports whose rows name a trial or play of the item |
| `enrollment` | per study and set with a list: planned units and persons from the list (main dyad slots for Study B), eligibility records and the participants they name, revealed units and persons, spares used and bank outages (Study B), date (UTC) of the last reveal-log line, reveal-log SHA-256; screening cases stay null (Pending) |

`reconciled/manifest.json` lists the four reconciled tables and every report;
`derived/manifest.json` the two derived tables and, among its inputs, the reports. The
inputs of both are every file of `raw/` and `inputs/` (never `keys/`).

## 9. Synthetic data roots

`synth-logs` (and `synthetic_logs.build_synthetic_root`) builds, from a public `DEMO-`
label, the schedules, lists and run sheets of `av_schedules.checks.build_set`, synthetic
packages for every main unit (their hashes fill the run sheets' `hash_check`), a reveal
log revealing the first `--units` units in list order (Study A capped by
`--max-persons`), and raw logs for every visit of the revealed persons: Study A learners
on day 0 and 7, both Study B dyad members on days 0, 2, 4, 11 and 32, the second member of
each acquisition visit five hours after the first. Plays follow the schedule's play
counts and slot timing (menus and lessons at the protocol's onsets), all
`confirmed_audible`; responses are random placeholders from `seeds.rng`; Study B profile
and atom choices are seeded (5% defaults) and committed through synthetic receipts and
snapshots. Every file is written through `paths.write_synthetic_input`. The same label
gives the same bytes on every platform.

## 10. Fault-injection suite

`synthetic_logs.inject_fault` rewrites the raw files (and exit manifest) of one visit;
with `documented` it also writes the record an operator would (the visit's
`deviations.csv`, or a `matching_deviation_id` on the play).

| Fault | Visit types | Injection | Expected code |
| --- | --- | --- | --- |
| `missing_trial` | all 7 | last trained trial and its plays removed, `actual_count` lowered (record: `technical`, `prior_audio_exposure` none) | `COUNT_MISSING_TRIAL` |
| `extra_play` | all 7 | last play of the first lesson (else trained) trial repeated (record: `audio`) | `COUNT_EXTRA_PLAY` |
| `wrong_hash` | all 7 | first trained trial and its play logged with another hash (record: `technical`) | `WAVEFORM_HASH_MISMATCH` |
| `holdout_in_lesson` | A D0, B V1-V3 | a complete held-out message (composable, not due at this visit) played in the first message lesson (record: `technical`) | `HOLDOUT_OUTSIDE_TEST` |
| `changed_old_atom` | B V2, V3, W1, W4 | the visit's store snapshot changes the PCM hash of a wave-1 atom (record: `technical`) | `OLD_ATOM_CHANGED` |
| `yoked_mismatch` | B V1-V3 | a rejected candidate play of the second member marked accepted (record: `matching`) | `YOKED_MISMATCH` |
| `late_visit` | A D7, B V2, V3, W1, W4 | run-sheet times 3 days later (both members for V2, V3; record: `window`) | `WINDOW_LATE` |
| `broken_retry_of` | all 7 | a retry of a trained trial that played normally (record: `procedure`) | `RETRY_LINK_BROKEN` |
| `unlinked_discrepancy` | all 7 | run-sheet `actual_count` off by one, never documented | `DEVIATION_MISSING` |

`fault_suite` runs each fault on every visit type it applies to, undocumented and
documented (95 cases): undocumented, the visit fails with the expected code and
`DEVIATION_MISSING`; documented, the expected code is resolved, C8 is clean and the visit
passes (suspension events are still reported). Result: 95 of 95 as expected
([`fault-suite.csv`](../examples/reconciliation-demo/fault-suite.csv)).

## 11. Discrepancy-code guide

For every code: what it means, where to look, how to resolve it. Never edit raw files:
corrections go into `raw/deviations-log.csv` (category `correction`) and the run is
repeated. "Deviation category" lists the categories that resolve the code when a record
names only the visit, person slot or participant; a record naming the row resolves any
code. In `raw/deviations-log.csv`, name a row as `<visit_id>/<row>` (for example
`A-P01-L01-D0/visit-run-sheet.csv:trained`); an unqualified row name there explains
nothing (section 6). A suspension event (red alert, analysis plan section 8) stops the affected
collection until it is resolved, even when explained.

### C1 raw-integrity

| Code | Meaning | Investigate | Resolve | Deviation category |
| --- | --- | --- | --- | --- |
| `RAW_MANIFEST_MISSING` | the visit folder has no exit manifest: hashes were not saved at exit | the station's export folder and sync receipts | re-import the export with its manifest; if it is lost, record a dated deviation (hashing now is not an exit record) | technical, procedure, correction |
| `RAW_FILE_MISSING` | a listed file or a required log is absent | the export and the import step | restore the file from the station's append-only store; else record a deviation | technical, procedure, correction |
| `RAW_FILE_UNLISTED` | a file in the folder is not in the exit manifest | who added it and when | move it out of `raw/`; or record why it was added after exit | technical, procedure, correction |
| `RAW_HASH_CHANGED` | a file's size or SHA-256 differs from the exit manifest; or (rows are data-root paths) a raw file changed, appeared or disappeared while reconciliation ran | compare with the station's copy; what wrote to `raw/` during the run | restore the exit version; corrections go to the deviations log; a change during the run is never explained: rerun on stable files | technical, correction (exit manifest only) |
| `RAW_FORMAT` | a header, value or identity column is invalid (detail names the column and rule), no run-sheet time, no `trial_ref`, or a deviation ID used by two records of the visit | the row named, the exporter version | fix the exporter or column adapter and re-export; record a deviation for affected rows; give a reused deviation ID a new ID through a `correction` record | technical, procedure, correction |
| `REFERENCE_INPUT` | a reference input is missing, invalid or not for this visit (detail names it) | `inputs/` against the frozen sets (hashes in their manifests) | place the frozen input and rerun; a slot not revealed means a wrong visit ID or reveal log | technical, procedure, correction |

### C2 counts

| Code | Meaning | Investigate | Resolve | Deviation category |
| --- | --- | --- | --- | --- |
| `COUNT_MISSING_TRIAL` | a scheduled trial has no trial-log row (plays naming it are listed) | operator notes, the station journal, interruptions | record why: `technical`/`audio` for an apparatus or logger failure (becomes a lost-opportunity row), `withdrawal` or `comfort` when the participant stopped (no row; battery partial) | technical, audio, withdrawal, comfort, procedure |
| `COUNT_EXTRA_TRIAL` | a trial-log row matches no scheduled trial and is not a retry | engine version, schedule file loaded | record the source of the extra trial | technical, audio, procedure |
| `COUNT_MISSING_PLAY` | a trial has fewer plays of its cue than scheduled | fault codes of the trial, audio journal | record the playback failure | technical, audio, procedure |
| `COUNT_EXTRA_PLAY` | a trial has more plays than scheduled, a play of another item, or a play names no trial | replays, restarts, the ledger rows listed | record the unplanned exposure (it stays in the learning history) | technical, audio, procedure |
| `COUNT_RUN_SHEET` | run-sheet blocks or counts differ from the generated sheet or from the logged trials | the operator's sheet | correct the count through a `correction` record | procedure, correction |
| `BLOCK_ORDER` | trials or blocks ran out of the scheduled order, or a trial ran another item | the schedule loaded, engine log | record a deviation; affected endpoints go to sensitivity analyses | procedure, technical |
| `RESPONSE_EVENT_MISSING` | a delivered test trial has no response code (or a commit has no time) and no `MISSING_RESPONSE_LOG` fault code | the response journal, controller logs | recover the event from the station journal; else record the missing response log (the trial has no valid delivery) | technical, procedure, correction |
| `TECHNICAL_FLAG_MISSING` | a no-onset, uncertain or unrequested cue, a freeze over 250 ms or a failed reset has no `technical_fault_code` (detail names the condition) | the exporter's fault mapping, the audio journal | record a deviation that names the fault; fix the exporter's fault mapping | technical, audio, correction |

### C3 waveform-hashes

| Code | Meaning | Investigate | Resolve | Deviation category |
| --- | --- | --- | --- | --- |
| `WAVEFORM_HASH_MISMATCH` (suspension `WRONG_FILE_MAPPING`) | a played hash equals neither expected hash of the item | the package loaded, the store (B), the item mapping | suspend the affected collection, preserve records, verify the package; record the deviation | technical, audio, correction |
| `WAVEFORM_HASH_MISSING` | a play has no hash and no PCM hash of composed audio | exporter, composed-audio logging | recover the hash from the station journal; else record a deviation | technical, audio, correction |
| `PACKAGE_HASH_MISMATCH` (suspension `WRONG_FILE_MAPPING`) | the package manifest or the run sheet's `hash_check` differs from the mapping | which package the station loaded | suspend, verify the package and the mapping; record the deviation | technical, correction |

### C4 exposure

| Code | Meaning | Investigate | Resolve | Deviation category |
| --- | --- | --- | --- | --- |
| `HOLDOUT_OUTSIDE_TEST` | a complete held-out message was heard outside its novel test | the stage and play listed | record it; its first exposure is consumed and later tests of it are repeats | corpus_exposure, technical, audio, procedure |
| `HOLDOUT_WRONG_VISIT` | a held-out message was first heard at another visit than scheduled | schedules, earlier visits | record it; the novelty endpoint of that phrase is affected | corpus_exposure, technical, audio, procedure |
| `HOLDOUT_REPEAT_AS_NOVEL` | a held-out trial logged as a first exposure after earlier audible or uncertain plays | the earlier plays (fold) | relabel through a `correction` record as a repeat, never as novel | technical, audio, correction |
| `UNCERTAIN_NOT_CONSUMED` | audible or uncertain audio logged with `exposure_consumed` false | onset evidence | record a correction: uncertain onset is consumed | technical, audio, correction |
| `PLAYBACK_STATUS_CONFLICT` | the trial's `playback_status` claims more delivery or less exposure than its plays' `audible_status` (detail lists both) | the audio journal and onset evidence, never a function's return value | record the verified delivery; uncertain onset counts as consumed | technical, audio, correction |
| `RETRY_LINK_BROKEN` | a retry names no trial, a trial without a verified no-onset failure, a retry or another item, is not at the end of the failed trial's block, or is a second retry | the original trial's plays and status, the trial order | record it; the retry cannot supply a first-exposure observation | technical, audio, procedure, correction |
| `ANSWER_DISPLAY_LEAK` (suspension `ANSWER_LEAK`) | feedback or the dictionary was available in a protected, pre-test or validity trial | UI log, scene state | suspend, preserve records, record the deviation | answer_leak, technical |

### C5 growth (Study B)

| Code | Meaning | Investigate | Resolve | Deviation category |
| --- | --- | --- | --- | --- |
| `OLD_ATOM_CHANGED` (suspension `OLD_WAVEFORM_CHANGED`) | an atom committed earlier has another entry in this snapshot | the bridge journal and receipts | suspend, restore the committed atom, record the deviation | technical, correction |
| `STORE_CHAIN_BROKEN` | receipts or snapshot do not chain, a self-hash differs, the profile changed, atoms of the wave are missing or an entry has no committed receipt | the bridge journal, the verify output | preserve store and receipts, verify the book before the next session; record the deviation | technical, correction |

### C6 yoked-ledger (Study B V1-V3)

| Code | Meaning | Investigate | Resolve | Deviation category |
| --- | --- | --- | --- | --- |
| `YOKED_SOURCE_MISSING` | a selection play has no counterpart in the pair's ledgers, or the other visit is not imported | the presentation ledger; whether the other session took place | import the other visit and rerun; else reconstruct the sequence before the next session and record a matching deviation | matching |
| `YOKED_MISMATCH` | matched plays differ (fields or timings listed: onset, audio duration, meaning-display duration, pause), are matched twice or out of order, or a selection play names a source inconsistently | the replayed ledger | record a matching deviation; the exposure is not matched | matching |
| `YOKED_GAP` | the second session did not start after the first ended and within 24 h of its start | session times of both visits | record a matching (or window) deviation | matching, window |

### C7 windows

| Code | Meaning | Investigate | Resolve | Deviation category |
| --- | --- | --- | --- | --- |
| `WINDOW_EARLY` | the visit took place before its window | scheduling record | record a window deviation; the planned in-window endpoint is missing | window |
| `WINDOW_LATE` | the visit took place after its window | scheduling record | record a window deviation; the visit enters the timing sensitivity only | window |
| `VISIT_ORDER` | the anchor or preceding visit is not held, or this visit is not on a later date | earlier visits, imports | import the earlier visit, or record the deviation | window, procedure, missed_visit |

### C8 deviation-links

| Code | Meaning | Investigate | Resolve | Deviation category |
| --- | --- | --- | --- | --- |
| `DEVIATION_MISSING` | the discrepancy with the same rows has no deviation record | the code it repeats | write the record (visit deviations or study-wide log) and rerun | none: never resolved by itself |
| `DEVIATION_UNKNOWN` | a row names a deviation ID that is in no record | typos, records not yet imported | add the record, or correct the reference with a `correction` record naming the row | correction |

## 12. Evidence and examples

- Tests: `tests/analysis/test_reconcile*.py` (the issue's `analysis/tests/test_reconcile.py`
  path maps to this folder): loaders, every check rule, clean roots for all 7 visit types,
  the fault suite (95 cases), derived tables and the commands. Rules beyond the issue's
  fault list have their own injected-fault tests: response events, technical flags,
  playback status, retry placement and run integrity (`test_reconcile_events.py`), the
  scope of deviation records and stale reports (`test_reconcile_scope.py`), C6 timings
  and role-neutral details (`test_reconcile_yoked.py`), and single C1, C3 and C5 rules
  (`test_reconcile_rules.py`), which also loads the sound stack's DEMO package JSON
  (`tests/analysis/fixtures/package-demo/`, copied from `sound/examples/package-demo/`).
- Sample reports: [`analysis/examples/reconciliation-demo/`](../examples/reconciliation-demo/README.md),
  one clean report per visit type and two fault examples, regenerated by the tests.
- CI: `analysis/ci/33.sh` builds a synthetic root, runs `refresh`, checks raw hashes
  before and after, times every visit and runs the fault suite; outputs are uploaded as
  `analysis-evidence-<os>`.
- Timing: about 7-21 ms per visit (pilot and confirmatory DEMO sets, Apple M3 Max); the
  proposed target is under 30 s on a lab laptop (not yet measured on one).

## 13. Decisions and open items

- **Play-to-trial link.** The exposure ledger gains the extension column `trial_ref`
  (section 3), written by `import-export` from the provisional `attempt_id` (#81).
- **Column adapter (#81).** Template columns without a provisional source are written
  empty and reported, never filled from the schedule (that would hide the mismatches C2
  looks for); the console reason -> category map and the null columns await agreement
  with #67, #72 and #73.
- **Visit-level deviation records** resolve only codes whose category fits (section 6),
  so a general note cannot explain an unrelated hash mismatch.
- **Study-wide log records are scoped to one visit** (section 6): a log record must name
  its visit (or the person or participant), and rows as `<visit_id>/<row>`, because row
  names recur across visits; a record naming another participant or unit never applies.
  Deviation records are looked up per visit in `derive` too, so a deviation ID reused by
  another visit cannot turn a verified technical loss into a withdrawal.
- **Response events and technical flags** (Study A protocol section 8, Common procedures
  sections 2 and 8) are reconciled per trial: `RESPONSE_EVENT_MISSING`,
  `TECHNICAL_FLAG_MISSING` (C2) and `PLAYBACK_STATUS_CONFLICT` (C4), appended to
  `codes.CODES`. Only a status that overstates delivery or understates exposure is a
  conflict; `uncertain` is accepted as conservative.
- **Retries** must run at the end of the failed trial's block (Common procedures section
  6), not merely before the next block.
- **C6 timings** compare onset, audio duration, meaning-display duration and pause within
  the same 100 ms (proposal), per Study B protocol sections 5.2 and 5.3.
- **Run integrity:** a raw file that changes during a run fails C1 with an unresolvable
  `RAW_HASH_CHANGED` and fails the report, so `raw_unchanged` false always exits 1.
- **Lost opportunity versus withdrawal** follows the deviation category of the record
  that resolves `COUNT_MISSING_TRIAL` (section 8).
- **C6 while the other visit is pending** reports `YOKED_SOURCE_MISSING` naming that
  visit; `refresh` after both imports clears it.
- **Not checkable (Pending):** hashes of the nonsemantic profile example (#64) and of
  speech commands (#71); per-atom recipe identity in the store (#70, #26); the append-only
  prefix of `raw/deviations-log.csv` across runs (data-lock manifests archive its hash);
  screening cases in `enrollment` (#73).
- **Pending agreements:** the adapter's null columns, reason map and values with #67, #72
  and #73; lesson and assessment tables; the exit manifest projection (implemented by
  `import-export`, not yet agreed); a native export reconciled; a timing run on a lab
  laptop.
