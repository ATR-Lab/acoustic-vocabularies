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

Raw files are opened read-only. `reconcile` hashes every raw file it reads before and
after the run; the report's `raw_unchanged` is true only when nothing changed.

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

## 3. Inputs

### Raw visit folder

`raw/<visit_id>/` holds `trial-log.csv`, `exposure-ledger.csv`, `visit-run-sheet.csv`,
`deviations.csv` (methodology template headers) and `exit-manifest.json`. The study-wide
`raw/deviations-log.csv` holds later deviations and corrections.

**Extension column `trial_ref` (proposal, Pending #72 adapter).** The exposure-ledger
template has no column that links a play to its scheduled opportunity. The provisional
export has one (`attempt_id`), so the exposure ledger may end with `pcm_sha256` and
`trial_ref` (`templates.EXTENSION_COLUMNS`): the trial-log `trial_id` of the attempt that
requested the play. Without it, plays cannot be counted per trial: C1 reports
`RAW_FORMAT` and the play-count rules of C2 are skipped.

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
| C1 raw-integrity | exit manifest present (`RAW_MANIFEST_MISSING`) and valid for this visit (`RAW_FORMAT`); every listed file present (`RAW_FILE_MISSING`) with the listed size and SHA-256 (`RAW_HASH_CHANGED`); no unlisted file (`RAW_FILE_UNLISTED`); the four logs present (`RAW_FILE_MISSING`); every header and value valid, identity columns equal to the visit, the exit manifest and the reveal log, at least one run-sheet `start_time`, the `trial_ref` column present, deviation IDs not reused between the visit file and the study-wide log (`RAW_FORMAT`; study-wide log lines only when they name this visit); reference inputs (`REFERENCE_INPUT`) |
| C2 counts | every scheduled trial has a trial-log row (`COUNT_MISSING_TRIAL`; plays that name it are listed); no unscheduled row that is not a retry (`COUNT_EXTRA_TRIAL`); a row's type and cue equal the schedule's, rows in scheduled order (longest increasing run kept, the rest reported per block) and blocks started in order (`BLOCK_ORDER`); per trial, plays of its cue equal the scheduled plays (lessons 3, menus 8, tests 1, no-cue 0) and no play of another item (`COUNT_MISSING_PLAY`, `COUNT_EXTRA_PLAY`); unlinked plays (`COUNT_EXTRA_PLAY`); run-sheet blocks and expected counts equal the generated sheet and `actual_count` equals the logged scheduled trials of the block (`COUNT_RUN_SHEET`) |
| C3 waveform-hashes | the package manifest's hash and the run sheet's `hash_check` equal the mapping (`PACKAGE_HASH_MISMATCH`); each trial's logged hash and each play's hash equals the PCM or the file hash of the item (`WAVEFORM_HASH_MISMATCH`): Study A from `audio.json`; Study B the option of the candidate (menus) or of the committed rank in the visit's snapshot (atoms) and the combination of committed ranks (messages); an empty hash only for composed audio with a `pcm_sha256` value (`WAVEFORM_HASH_MISSING`). Nonsemantic profile examples and speech commands have no package hash and are not checked (Pending #64, #71) |
| C4 exposure | no complete held-out message in any stage but `novel` (`HOLDOUT_OUTSIDE_TEST`); a held-out message's first consuming play happens at the visit whose novel block schedules it (`HOLDOUT_WRONG_VISIT`, reported at the visit where it happened); a held-out trial logged with `prior_complete_phrase_exposures` 0 after earlier consuming plays (`HOLDOUT_REPEAT_AS_NOVEL`); audible or uncertain audio with `exposure_consumed` false (`UNCERTAIN_NOT_CONSUMED`); a retry must name a scheduled trial of the visit whose plays are all `confirmed_no_onset`, be the only retry, not retry a retry, and stay in that trial's block with the same cue (`RETRY_LINK_BROKEN`); feedback or dictionary in a pre-old, protected or validity trial, or feedback content with a test play (`ANSWER_DISPLAY_LEAK`) |
| C5 growth (B) | receipt self-hashes and the `before_head`/`after_head` chain from the profile receipt on, rejected receipts not moving the head; the snapshot's self-hash (`manifest_sha256`), its `book_head` in the chain and not before the previous snapshot's; profile and profile receipt unchanged since the first snapshot; the atoms of this visit's wave present and new entries backed by a committed receipt (`STORE_CHAIN_BROKEN`); every atom committed at an earlier visit keeps its committing snapshot's entry: profile, rank, PCM and file hash, receipt (`OLD_ATOM_CHANGED`) |
| C6 yoked-ledger (B V1-V3) | evaluated once per dyad over both members' ledgers, identical in both reports: every selection play (profile and atom menus) of the second member names a selection play of the first (`YOKED_SOURCE_MISSING`) and equals it in stage, item, candidate, acceptance, hash, phrase flag, presentation index, meaning display, choice flag and onset timing within 100 ms (`YOKED_MISMATCH`); every source play has exactly one copy, in the same order (`YOKED_SOURCE_MISSING`, `YOKED_MISMATCH`); the second session starts after the first ended and within 24 h of its start (`YOKED_GAP`); while the other member's visit has no raw logs, `YOKED_SOURCE_MISSING` names that visit |
| C7 windows | not applicable to anchor visits (A D0, B V1); the anchor and the preceding visit must be held with a date (`VISIT_ORDER`); `windows.classify` early or late (`WINDOW_EARLY`, `WINDOW_LATE`); the visit on a later date than the visit that must precede it (`VISIT_ORDER`) |
| C8 deviation-links | every unresolved discrepancy of C1-C7 gives `DEVIATION_MISSING` with the same rows; a trial-log `deviation_id` or ledger `matching_deviation_id` that is not in the visit's deviations or the study-wide log gives `DEVIATION_UNKNOWN` (resolved by a `correction` record naming the row) |

## 6. Deviation links

A discrepancy is resolved by the first match, in this order:

1. a trial-log `deviation_id` or ledger `matching_deviation_id` on a row it names, when
   that record exists;
2. a record whose `event_id` is one of its rows (a trial or event ID), any category;
3. a record whose `event_id` is the visit ID, then one whose `event_id` is the person slot,
   then one with empty `event_id` and the coded participant ID, each only with a category
   that fits the code (`reconcile_checks.LINK_CATEGORIES`, column "Deviation category" in
   the guide below).

Records are the visit's `deviations.csv` and `raw/deviations-log.csv`; C6 also uses the
other member's records, visit and slot. Among equal matches the smallest deviation ID
wins. `DEVIATION_MISSING` is never resolved: write the record and rerun.

## 7. Report

`reconciled/<visit_id>/reconciliation.json` ([schema](../schema/reconciliation.schema.json)):
identity, `inputs` (every file read, with size and SHA-256), `raw_unchanged`, `checks`
(C1..C8: status, number of discrepancies, unresolved), `discrepancies` (`seq` in check
order then by rows; code, rows, deviation link, resolved, suspension event, detail) and
`summary` (`status` `pass` unless a check fails; `discrepancies` and `unresolved` count
C1-C7 and `DEVIATION_UNKNOWN`; suspension events of all of them, resolved or not). Check
status: `pass` none, `explained` all resolved, `fail` any unresolved, `not_applicable`.
Details name rows, item IDs and rules only.

## 8. Tables (`derive`)

`derive` refuses a report whose listed inputs changed since it was written. Persons are
the slots bound in the reveal logs; a visit is held when its raw folder exists.

| Table | Rule |
| --- | --- |
| `trials` | one row per trial-log row of a reconciled visit that is a scheduled trial or a retry linked to a logged one (extra trials are left out). Schedule fields and the private intended labels come from the schedule (a retry takes its original's). `valid_delivery`: verified playback (`observed_complete`, the scheduled number of linked plays, all `confirmed_audible` or `estimated`; always for no-cue trials), a response code for test trials, no fault type, and no hash, play-count discrepancy on the trial or its plays. `exposure_consumed`: any linked play consumed exposure (without linked plays: `observed_complete` or `uncertain`). Prior counts and `novelty` from the fold. `deviation_ids`: the row's own known `deviation_id` and the links of resolved discrepancies naming the trial or its plays; `discrepancy_codes`: their codes. Lost opportunities: each `COUNT_MISSING_TRIAL` resolved by a `technical` or `audio` record is a `row_source` deviation row (`OPPORTUNITY_LOST`, fault type `other`, `valid_delivery` false, response fields null, `exposure_consumed` unless the record says `prior_audio_exposure` `none`) |
| `endpoints` | per person, visit and battery block of the schedule (also for visits not held): `accounted_n` = trials rows that are not retries (lost rows included), `fault_n` those with fault codes or types, `lost_n`, `valid_delivery_n` (valid, or its retry valid), `retry_n`; status `complete`, `partial`, `missing`; `missing_reason`: the visit state for visits not held (`withdrawn`, `missed`, `pending`), `pending` for a held visit not reconciled yet, `withdrawn_mid_battery` when a missing trial of the battery is resolved by a `withdrawal` or `comfort` record, `technical_stop` when one is unresolved and the exit manifest says `interrupted`, else `other`; `planned_endpoint` = complete and in window (or anchor) |
| `visit-status` | every expected visit of every revealed person; `visit_state` `held`, else `withdrawn` (a `withdrawal` record naming the slot, the participant, or this or an earlier visit), `missed` (a `missed_visit` record naming the visit), `pending`; reconciliation and failed checks from the report (`not_run` without one); timing from the first run-sheet start of the visit and of its anchor; `pair_gap_hours` (absolute hours between the members' session starts) and `pair_gap_ok` (`windows.yoked_gap_ok`) for Study B V1-V3, identical on both rows; `actual_minutes` first start to last end; `overrun` beyond booking + 10 min; opportunity and fault counts from `trials` (not retries); deviation counts from the visit's records plus study-wide records naming the visit, a row or play of it |
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
code. A suspension event (red alert, analysis plan section 8) stops the affected
collection until it is resolved, even when explained.

### C1 raw-integrity

| Code | Meaning | Investigate | Resolve | Deviation category |
| --- | --- | --- | --- | --- |
| `RAW_MANIFEST_MISSING` | the visit folder has no exit manifest: hashes were not saved at exit | the station's export folder and sync receipts | re-import the export with its manifest; if it is lost, record a dated deviation (hashing now is not an exit record) | technical, procedure, correction |
| `RAW_FILE_MISSING` | a listed file or a required log is absent | the export and the import step | restore the file from the station's append-only store; else record a deviation | technical, procedure, correction |
| `RAW_FILE_UNLISTED` | a file in the folder is not in the exit manifest | who added it and when | move it out of `raw/`; or record why it was added after exit | technical, procedure, correction |
| `RAW_HASH_CHANGED` | a file's size or SHA-256 differs from the exit manifest | compare with the station's copy | restore the exit version; corrections go to the deviations log | technical, correction |
| `RAW_FORMAT` | a header, value or identity column is invalid (detail names the column and rule), no run-sheet time, no `trial_ref`, or a reused deviation ID | the row named, the exporter version | fix the exporter or column adapter and re-export; record a deviation for affected rows | technical, procedure, correction |
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
| `RETRY_LINK_BROKEN` | a retry names no trial, a trial without a verified no-onset failure, a retry, another block, or is a second retry | the original trial's plays and status | record it; the retry cannot supply a first-exposure observation | technical, audio, procedure, correction |
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
| `YOKED_MISMATCH` | matched plays differ (fields listed), are matched twice or are out of order | the replayed ledger | record a matching deviation; the exposure is not matched | matching |
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
  the fault suite (95 cases), derived tables and the commands.
- Sample reports: [`analysis/examples/reconciliation-demo/`](../examples/reconciliation-demo/README.md),
  one clean report per visit type and two fault examples, regenerated by the tests.
- CI: `analysis/ci/33.sh` builds a synthetic root, runs `refresh`, checks raw hashes
  before and after, times every visit and runs the fault suite; outputs are uploaded as
  `analysis-evidence-<os>`.
- Timing: about 7-21 ms per visit (pilot and confirmatory DEMO sets, Apple M3 Max); the
  proposed target is under 30 s on a lab laptop (not yet measured on one).

## 13. Decisions and open items

- **Play-to-trial link.** The exposure ledger gains the extension column `trial_ref`
  (section 3); proposal to #72 for the column adapter (`attempt_id`).
- **Visit-level deviation records** resolve only codes whose category fits (section 6),
  so a general note cannot explain an unrelated hash mismatch.
- **Lost opportunity versus withdrawal** follows the deviation category of the record
  that resolves `COUNT_MISSING_TRIAL` (section 8).
- **C6 while the other visit is pending** reports `YOKED_SOURCE_MISSING` naming that
  visit; `refresh` after both imports clears it.
- **Not checkable (Pending):** hashes of the nonsemantic profile example (#64) and of
  speech commands (#71); per-atom recipe identity in the store (#70, #26); the append-only
  prefix of `raw/deviations-log.csv` across runs (data-lock manifests archive its hash);
  screening cases in `enrollment` (#73).
- **Pending agreements:** the column adapter and values with #67, #72 and #73; the exit
  manifest projection; a timing run on a lab laptop.
