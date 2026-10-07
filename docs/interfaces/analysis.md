# Analysis interface (`av_analysis`)

Producers: reconciliation (#33), analysis pipeline (#34), integrity dashboard (#35).
Consumers: operators and the study coordinator (reconciliation reports, dashboard),
data-lock gates O7.3.1 and O8.3.1 (reports and output manifests), sample-size decisions
O6.4.1 and O6.4.3 (pipeline and simulation harness), pilot reviews O6.3.4 and O6.3.5
(fault and overrun rates). Design, module ownership and rules:
[`analysis/docs/architecture.md`](../../analysis/docs/architecture.md).

The package (`analysis/`, distribution `av-analysis`, Python 3.11, `uv` project) depends
on `av-schedules` by path. It reads every other component through files only. It is
deterministic: no network, no wall clock, seeds stored in the outputs.

## Shared contracts

### Data roots

Every command works on a data root marked by `av-data-root.json`
([schema](../../analysis/schema/data-root.schema.json)): `data_kind` `SYNTHETIC` (label
`DEMO-...`) or `REAL` (outside any committable git path). Create one with
`av-analysis init-root DIR --kind SYNTHETIC|REAL --label LABEL [--study] [--set]`.

| Area | Access | Content |
| --- | --- | --- |
| `raw/<visit_id>/` | read-only | `trial-log.csv`, `exposure-ledger.csv`, `visit-run-sheet.csv`, `deviations.csv` (methodology template headers) and `exit-manifest.json` |
| `raw/deviations-log.csv` | read-only | study-wide append-only deviations and corrections (deviations template) |
| `inputs/` | read-only | reference inputs, paths below |
| `keys/` | read-only, #34 only | `A/<set>-book-key.json` |
| `reconciled/` | #33 | `<visit_id>/reconciliation.json`, `visit-status.csv`, `discrepancies.csv`, `exposure-cumulative.csv`, `enrollment.csv`, `manifest.json` |
| `derived/` | #33 | `trials.csv`, `endpoints.csv`, `manifest.json` |
| `estimates/` | #34 | section 9 report, tables, GLMM logs, `manifest.json` |
| `monitoring/` | #35 | `index.html`, `manifest.json` |

`visit_id` = `<person_id>-<visit>`: `A-C07-L03-D0`, `B-C12-M1-W1` (person slots of the
schedules; coded participant IDs appear only in raw logs and the reveal log).

Reference inputs (`paths.INPUT_PATHS`; `inputs/schedules/` is a copy of the schedules
output folder of the set, without the book key):

| Key | Path |
| --- | --- |
| `schedule` | `inputs/schedules/{study}/{unit_id}/schedules/{person_id}/{visit}.json` |
| `run_sheet` | `inputs/schedules/{study}/{unit_id}/run-sheets/{person_id}/{visit}.csv` |
| `slots`, `dyads` | `inputs/schedules/A/{set}-slots.json`, `inputs/schedules/B/{set}-dyads.json` |
| `package_hashes` | `inputs/schedules/{study}/{set}-package-hashes.json` |
| `reveal_log` | `inputs/reveal/{study}-{set}.jsonl` |
| `package_manifest`, `package_audio` | `inputs/packages/{package_id}/manifest.json`, `.../audio.json` (JSON only) |
| `store_snapshot` | `inputs/store-snapshots/{unit_id}/{visit}.json` (Study B: the menu-store bridge's `verified_snapshot` after the visit's selections) |
| `store_receipts` | `inputs/store-snapshots/{unit_id}/receipts.jsonl` (Study B: the bridge's selection receipts, in order) |
| `golden_manifest` | `inputs/sound/golden-manifest.json` |
| `generation_audit` | `inputs/generation/{study}-{set}-audit.csv` (**Pending** #24) |
| `book_key` | `keys/A/{set}-book-key.json` |

**Exit manifest** ([schema](../../analysis/schema/exit-manifest.schema.json)): `format`
`av-analysis/exit-manifest`, `format_version` 1, `data_kind`, `visit_id`, `session_id`,
`station_id`, `closed` (`complete`/`interrupted`), `source` and `files` (`path`, `bytes`,
`sha256` of every other file in the folder). It is a projection of the data logger's
ExportBundle manifest (#72, `docs/data/README.md`), written when an export is imported
into a data root (**Pending** agreement with #72/#73); the synthetic generator writes
`source` null, a REAL root requires it.

| Exit manifest | ExportBundle manifest (`data-export-provisional-1`) |
| --- | --- |
| `session_id`, `station_id` | `identity.session_id`, `identity.station_id` |
| `visit_id` | person slot bound to `identity.coded_id` in the reveal log, plus the visit |
| `closed` | `interrupted` when `unacknowledged_torn_tail` is true or the journal has no `visit_complete` record, else `complete` |
| `source.format`, `source.export_id` | `schema_version`, `export_id` |
| `source.manifest_sha256` | SHA-256 of the export manifest bytes (supplied out of band) |
| `source.protocol_version`, `source.build_sha256` | `identity.protocol_version`, `identity.build_sha256` |
| `source.headers_qualified`, `source.unacknowledged_torn_tail` | same names |
| `source.record_count`, `source.trial_rows`, `source.exposure_rows` | same names |
| `files` | the template CSVs written by the column adapter, the operator's run sheet and deviations, and any export files kept (for example under `export/`), hashed at import |

**Watermark.** JSON outputs carry top-level `data_kind`; CSV outputs a first column
`data_kind`; HTML `<meta name="av-data-kind" content="SYNTHETIC|REAL">`; Markdown a line
`av-data-kind: SYNTHETIC|REAL`; synthetic HTML and Markdown a visible `SYNTHETIC` banner.
`paths.write_output` refuses anything else and any write into a root of the other kind.
`raw/`, `inputs/` and `keys/` are written only by the synthetic generators, through
`paths.write_synthetic_input` (SYNTHETIC roots only, content marked as real refused).

### Raw-log values (**Pending** confirmation by #67, #72, #73)

Where the provisional data logger on main already emits a value (#72,
`data-csv-provisional-1`), the analysis accepts the producer's value; the column adapter
from the provisional headers to the template headers is still **Pending**.

| Column | Values | Source |
| --- | --- | --- |
| trial log `playback_status` | `observed_complete`, `uncertain`, `confirmed_no_onset`, `not_requested` | producer |
| exposure ledger `audible_status` | `confirmed_audible`, `estimated`, `uncertain`, `confirmed_no_onset` (all but the last consume exposure) | producer |
| trial log `response_code` | `commit`, `dont_know`, `timeout`; empty = no response recorded | producer |
| `technical_fault_code` | empty, or codes `^[A-Z][A-Z0-9_]{0,79}$` joined by `;`; fault type by `vocab.fault_type` (`AUDIO_UNDERRUN`, `MISSING_PLAYBACK`, `HASH_MISMATCH`, `FAILED_RESET`, `MISSING_RESPONSE_LOG`, `PRESENTATION_FREEZE` and known producer codes; any other code: `other`); also `presentation_freeze` when `frame_freeze_ms` > 250 and `failed_reset` when `reset_ok` is `false` | producer codes; mapping proposal |
| `waveform_sha256` | file SHA-256 for playback from a package file; PCM-sample SHA-256 (`composite_sha256`, `pcm_sha256`) for audio composed in memory; empty only for composed audio with a `pcm_sha256` extension value | **Pending** #64/#72 |
| extension columns | trial log may end with `pcm_sha256`; exposure ledger with `pcm_sha256`, `trial_ref` (the `trial_id` of the attempt that requested the play), in that order, each optional (`templates.EXTENSION_COLUMNS`) | proposal |
| run sheet `start_time`, `end_time`; deviations `timestamp` | ISO 8601 with seconds and a UTC offset, e.g. `2027-03-01T09:30:00+01:00` (`Z` for UTC); naive times refused | proposal |
| deviations `operator`, `reviewer`; run sheet `operator_signoff` | coded staff IDs `^[A-Z]{1,3}[0-9]{2,4}$` (e.g. `S03`), never names; never copied to outputs | proposal |
| run sheet `comfort_check` | `ok`, `adjusted`, `stopped` | proposal |
| deviations `category` | `technical`, `audio`, `matching`, `window`, `missed_visit`, `withdrawal`, `comfort`, `corpus_exposure`, `answer_leak`, `procedure`, `correction`, `other` | proposal |
| booleans | `true`, `false` | producer |

### Tables

Row schemas (generated from `derived.TABLES`, typed rows):
[`trials`](../../analysis/schema/trials-row.schema.json),
[`endpoints`](../../analysis/schema/endpoints-row.schema.json),
[`visit-status`](../../analysis/schema/visit-status-row.schema.json),
[`discrepancies`](../../analysis/schema/discrepancies-row.schema.json),
[`exposure-cumulative`](../../analysis/schema/exposure-cumulative-row.schema.json),
[`enrollment`](../../analysis/schema/enrollment-row.schema.json).
CSV: UTF-8, `\n`, header = column names in schema order (`required`), first column
`data_kind`, empty cell = null, `true`/`false`, decimal integers, floats as shortest
round-trip decimal, dates `YYYY-MM-DD`, lists joined by `|`, rows sorted and unique on
the table key. Read and write them with `derived.parse_table` / `derived.table_bytes`.

`trials` has one row per trial-log row and one per scheduled opportunity lost to an
apparatus or logger failure that a deviation record verifies (`row_source` `deviation`:
fault code `OPPORTUNITY_LOST`, `valid_delivery` false, response fields null; operational
score 0). `endpoints.accounted_n` and `fault_n` include them (`lost_n`). An opportunity
never undertaken after withdrawal has no row (`missing_reason` `withdrawn_mid_battery`);
`technical_stop` marks a technical stop without a verifying deviation record.

### Checks and discrepancy codes

| Check | Name | Codes (suspension event) |
| --- | --- | --- |
| C1 | raw-integrity | `RAW_MANIFEST_MISSING`, `RAW_FILE_MISSING`, `RAW_FILE_UNLISTED`, `RAW_HASH_CHANGED`, `RAW_FORMAT`, `REFERENCE_INPUT` |
| C2 | counts | `COUNT_MISSING_TRIAL`, `COUNT_EXTRA_TRIAL`, `COUNT_MISSING_PLAY`, `COUNT_EXTRA_PLAY`, `COUNT_RUN_SHEET`, `BLOCK_ORDER`, `RESPONSE_EVENT_MISSING`, `TECHNICAL_FLAG_MISSING` |
| C3 | waveform-hashes | `WAVEFORM_HASH_MISMATCH`, `PACKAGE_HASH_MISMATCH` (both `WRONG_FILE_MAPPING`), `WAVEFORM_HASH_MISSING` |
| C4 | exposure | `HOLDOUT_OUTSIDE_TEST`, `HOLDOUT_WRONG_VISIT`, `HOLDOUT_REPEAT_AS_NOVEL`, `UNCERTAIN_NOT_CONSUMED`, `RETRY_LINK_BROKEN`, `ANSWER_DISPLAY_LEAK` (`ANSWER_LEAK`), `PLAYBACK_STATUS_CONFLICT` |
| C5 | growth (B) | `OLD_ATOM_CHANGED` (`OLD_WAVEFORM_CHANGED`), `STORE_CHAIN_BROKEN` |
| C6 | yoked-ledger (B) | `YOKED_SOURCE_MISSING`, `YOKED_MISMATCH`, `YOKED_GAP` |
| C7 | windows | `WINDOW_EARLY`, `WINDOW_LATE`, `VISIT_ORDER` |
| C8 | deviation-links | `DEVIATION_MISSING`, `DEVIATION_UNKNOWN` |

`codes.CODES` holds each code's title, description and first resolution step; the list
may grow (consumers render codes generically). C3 compares a logged hash with both
expected hashes of the scheduled item (`references.ExpectedHash`: PCM or composite hash,
and the file hash, `null` for composed audio). C5 compares, for every atom of an earlier
wave, the `verified_snapshot` entry (profile, rank, PCM and file hash, selection receipt)
across later snapshots and checks that the receipts' `before_head`/`after_head` chain links
the snapshots' `book_head` values; per-atom recipe identity is **Pending** (#70, #26). C6
is evaluated once per dyad and visit and written identically into both members' reports. Windows (`windows.WINDOWS`): A D7 6-8 days
after D0; B V2 1-3 and V3 3-5 days after V1 (V3 after V2); W1 6-8 and W4 26-30 days after
the person's V3; yoked acquisition sessions after the active one ended and within 24 h of
its start.

### Masking policies

`masking.forbidden_reason(field, policy)`: policy `masked` (reports, reconciled tables,
everything the dashboard reads or renders) forbids outcome, response, response-time,
hidden-answer, rating, condition and personal fields; policy `derived` (derived tables)
forbids condition and personal fields. Personal fields include the staff template
columns `operator`, `reviewer` and `operator_signoff`.

### Command line

`av-analysis` (`uv run --project analysis av-analysis ...` or `python -m av_analysis`):
`init-root`, `schemas [--write]`, `check-templates [DIR]`, `refresh --root DIR`
(skeleton); `synth-logs`, `reconcile`, `derive` (#33); `run`, `simulate` (#34);
`dashboard` (#35). Exit codes: 0 success, 1 findings, 2 refused input, 3 not implemented
yet. `refresh` is the operator sequence after a visit: `reconcile --all`, `derive`,
`dashboard`; it continues after findings, stops at a refused input and skips a command
that is not implemented yet.

### Schemas published by issue modules

`analysis/schema/` holds the core schemas above and any schema an issue module publishes
through a module-level `SCHEMAS` mapping (`"<name>.schema.json"` -> function returning the
document, `$id` ending in that name); `av-analysis schemas` collects them, so an issue
adds a schema (for example #34's operating-characteristics rows, #35's dashboard data)
without editing `schemas.py`.

## Reconciliation (#33)

Producer: reconciliation scripts (#33). Consumers: #34 (derived tables), #35 (reconciled
tables), data-lock gates O7.3.1 and O8.3.1 (reports and manifests), engineering-pilot
check 8 (Common procedures section 2). Design, rules and the discrepancy-code guide:
[`analysis/docs/reconciliation.md`](../../analysis/docs/reconciliation.md). Sample
reports: [`analysis/examples/reconciliation-demo/`](../../analysis/examples/reconciliation-demo/README.md).

Commands (exit 0 success, 1 findings, 2 refused input):

- `av-analysis reconcile <visit_id>... --root DIR` (or `--all`): checks C1-C8 per visit,
  `reconciled/<visit_id>/reconciliation.json`
  ([schema](../../analysis/schema/reconciliation.schema.json)): `inputs` (every file read,
  with size and SHA-256), `raw_unchanged`, `checks` (C1..C8: `status` `pass`,
  `explained`, `fail`, `not_applicable`; counts), `discrepancies` (`seq`, `check`,
  `code`, `rows`, `deviation_id`, `resolved`, `suspension_event`, `detail`), `summary`.
  Same inputs, same bytes. Exit 1 when a visit fails.
- `av-analysis derive --root DIR`: the six tables and `reconciled/manifest.json`,
  `derived/manifest.json`; refuses a report whose inputs changed since it was written, or
  older than an input a rerun would read (a raw file of the visit, its earlier visits or
  the partner visit, the study-wide log, a reference input it reported missing).
- `av-analysis synth-logs --demo-seed DEMO-... --out DIR [--study] [--set] [--units N]
  [--max-persons N]`: a SYNTHETIC root with clean logs for every visit type;
  `--fault NAME --visit ID [--documented]` injects a fault (`codes.FAULT_INJECTIONS`);
  `--fault-suite` runs all of them; `--examples` rewrites the sample reports.

Raw-log rules beyond `vocab` (Pending confirmation by #67, #72, #73; full table in the
guide): trial-log `message_id` is the cue (message, atom or speech ID; empty for the
profile menu and no-cue trials); exposure-ledger `stage` is the trial type of the play's
opportunity (or `practice`), `atom_or_message_id` the item heard (menus: the atom),
`candidate_id` `<atom_id>-<rank>` or the preset `P1`..`P3`, `presentation_index` the play
number within its trial, and `trial_ref` links the play to its trial; deviations
`prior_audio_exposure` is `none`, `audible` or `uncertain` (empty: uncertain), and
`event_id` names a row as reports do, qualified as `<visit_id>/<row>` in
`raw/deviations-log.csv`.

Deviation links: a row-level link (`deviation_id`, `matching_deviation_id`) or a record
whose `event_id` names a row resolves any code; a record naming only the visit, person
slot or participant resolves codes whose category fits
(`reconcile_checks.LINK_CATEGORIES`). Only records that concern the visit count
(`reconcile_checks.visit_records`): the visit's `deviations.csv`, and study-wide log
records that name no other participant or unit and whose `event_id` is the visit ID, the
person slot, `<visit_id>/<row>`, a row ID starting with `<visit_id>-`, or empty with the
coded participant ID. Unresolved discrepancies fail the visit through C8
`DEVIATION_MISSING`; a raw file changed during the run fails C1 (`RAW_HASH_CHANGED`,
never resolved) and `raw_unchanged`.

How the tables are computed (details in the guide, section 8):

- `trials`: scheduled trials and retries linked to a logged trial; `valid_delivery` needs
  the scheduled linked plays all `confirmed_audible` or `estimated` with
  `observed_complete` (no-cue trials: always), a response code for test trials, no fault
  type and no hash, play-count, playback-status or response-event discrepancy; prior counts and `novelty` from the
  exposure fold (plays linked by `trial_ref`, trial-log order); a `COUNT_MISSING_TRIAL`
  resolved by a `technical` or `audio` record becomes a `row_source` deviation row.
- `endpoints`: `accounted_n` counts trials rows that are not retries (lost rows
  included); `valid_delivery_n` counts an opportunity valid also when its retry is;
  `missing_reason` `withdrawn_mid_battery` when a missing trial is resolved by a
  `withdrawal` or `comfort` record, `technical_stop` when unresolved in an interrupted
  export.
- `visit-status`: `withdrawn` and `missed` from `withdrawal` and `missed_visit` records;
  `pair_gap_hours` is the absolute gap between the two members' session starts;
  deviation counts include the study-wide records that concern the visit.
- `exposure-cumulative`: counts of plays that consumed exposure, by phase;
  `violations` are the C4 codes of the person's reports naming the item.
- `enrollment`: from the reveal logs; `screening_cases_n` null (Pending #73).

Not checkable yet (Pending): nonsemantic profile-example and speech-command hashes (#64,
#71), per-atom recipe identity (#70, #26), the deviations log's append-only prefix
across runs, screening cases.

## Analysis pipeline (#34)

Producer: analysis pipeline on synthetic data (#34). Consumers: sample-size decisions
(O6.4.1, O6.4.3: the pipeline and the simulation harness), confirmatory analyses
(O7.3.2, O8.3.2: the frozen pipeline version and its hash). Design, rules and decisions:
[`analysis/docs/pipeline.md`](../../analysis/docs/pipeline.md).

- `av-analysis run --study A|B --data DIR [--set pilot|confirmatory] [--glmm
  auto|require|skip]`: reads `derived/trials.csv`, `derived/endpoints.csv`, the
  allocation lists and book key (`unmask`, the only reader of `keys/`), the reveal log
  when present and, when present, `reconciled/visit-status.csv`,
  `reconciled/discrepancies.csv`, `reconciled/enrollment.csv` (report section 1:
  pre-allocation eligibility and enrollment counts) and
  `inputs/sound/golden-manifest.json`. Refuses (exit 2) a held visit whose
  `reconciliation` is not `pass`, persons, units or books that differ from the lists,
  rows that cannot be scored, inputs of the other data kind, an `enrollment` row whose
  `reveal_log_sha256` differs from the reveal log read.
- Writes into `estimates/` of the same root (watermarked, `paths.write_output`):

  | File | Content |
  | --- | --- |
  | `report-<study>.md` | section 9 report in `report.REPORT_SECTIONS` order: flow, fidelity, A primary or B primary, secondary, ownership and consultation (**Pending** source), sensitivities, deviations and bounded conclusions; every table followed by its `report.TableMeta` line (independent unit, contributing and planned units, trials, missing units) |
  | `tables/<study>-<nn>-<id>.csv` | each table in full, first column `data_kind`; percentages and percentage points as named in the column |
  | `tables/<study>-index.csv` | `data_kind`, `table`, `section`, `file`, `title`, `independent_unit`, `units`, `units_planned`, `trials`, `missing`, `note` |
  | `glmm/<model_id>.json` | [GLMM log](../../analysis/schema/glmm-log.schema.json): every rung tried or skipped in ladder order with formula, converged and singular flags, R messages, reason; R and lme4 versions |
  | `glmm/<model_id>-fixed.csv`, `-random.csv`, `-data.csv` | accepted rung's fixed effects (`term`, `estimate`, `se`, `z`, `p`, logit scale) and random-effect SDs and correlations; the model data sent to R |
  | `runs/run-<study>-<set>.json` | inputs (path, bytes, SHA-256), seed labels, outputs |
  | `manifest.json` | [outputs manifest](../../analysis/schema/outputs-manifest.schema.json) of the whole area |

- Primary tables: `a-primary` (estimator, units planned and used, estimate, SD, SE, df,
  t, p, confidence level, interval in percentage points; the stratified bootstrap row);
  `b-primary` (C and S with 95% and 97.5% intervals and the Holm rank, threshold,
  adjusted p and decision); `a-secondary` (A3-A1, A2-A1 with Holm); `sens-bounds`
  (all-assigned bounds) and `sens-tipping-grid` (contrast, shifts, estimate,
  `direction_changed`, `practical_changed`; Study B also `companion_contrast` and
  `companion_estimate_pp`, the other contrast from the same imputed values);
  `flow-enrollment` (counts from `reconciled/enrollment.csv`).
- Supporting models (`glmm.model_specs`): `A-trained`, `A-designer` (random intercepts
  for batch, book, learner, message) and `B-trained` (dyad role slope, participant
  teaching-format slope, message intercept), fitted rung by rung with
  `analysis/r/glmm.R` through `rbridge.run_r` (`lme4::glmer`, pinned in
  `analysis/r/pins.dcf`: R 4.6.1, CRAN snapshot 2026-09-01, lme4 2.0-6). A rung is
  accepted when converged and not singular; without a stable rung the log ends with
  `descriptive`. `--glmm auto` requires R on REAL roots.
- `av-analysis simulate --scenario NAME[,NAME...]|all --datasets N --seed DEMO-... --out
  DIR [--write-dataset]`: scenarios `central-`, `pessimistic-`, `null-`,
  `null-pessimistic-` and `pilot-` for A and B, plus `null-co-B`, `null-scaffold-B`
  (`simulate.scenarios()`). Writes `estimates/simulation/operating-characteristics.csv`
  ([row schema](../../analysis/schema/operating-characteristics-row.schema.json):
  scenario, study, contrast, rule, true effect, datasets, rejections, rate, MCSE, Wilson
  95% Monte Carlo interval, mean estimate, mean unit SD, mean units, unavailable, seed),
  per-scenario `-operating-characteristics.csv`, `-datasets.csv` and `-scenario.json`.
  `--write-dataset` writes dataset 0 of each scenario into the root: `derived/trials.csv`
  and `derived/endpoints.csv` (merged with other studies' rows), `derived/manifest.json`,
  and the key and lists through `paths.write_synthetic_input`.
- Python interfaces: `simulate.simulate_dataset(scenario, seed, *, index=0) ->
  SyntheticDataset`, `simulate.operating_characteristics(scenario, seed, datasets)`,
  `pipeline.analyze(root, study, ...) -> report.StudyReport`,
  `pipeline.run_analysis(root, study, ...) -> list[Path]`,
  `unmask.load_conditions(root, study, set) -> Conditions` (with `person_book`,
  `list_sha256` and `planned_source`), `missingness.all_assigned_bounds(...)`,
  `tipping_grid(...)` (`TippingCell.companion`) and `tipping_imputations(..., cell) ->
  list[ImputedScore]` over `scoring.BatteryScore`.

**Pending (#34):** ratings and consultation export format (section 6 of the report),
generation fallback flags for the non-fallback sensitivity (#24), a timing model with
actual delay, real-data runs (out of scope).

## Integrity dashboard (#35)

Producer: integrity monitoring dashboard (#35). Consumers: study coordinator and
research assistants (masked staff), pilot reviews. Design and reading guide:
`analysis/docs/monitoring.md`.

- `av-analysis dashboard --root DIR`: `monitoring/index.html` (static, watermarked) from
  `reconciled/visit-status.csv`, `reconciled/discrepancies.csv` and
  `reconciled/enrollment.csv` only, through an explicit column allowlist
  (`monitoring.allowlist()`); panels `monitoring.PANELS` (alerts, enrollment, allocation,
  attrition, windows, faults, reconciliation). Comfort and welfare come from
  `visit-status` `comfort_flag`, `comfort_deviations_n` and `withdrawal_deviations_n`;
  faults by type from `fault_<type>_n` (`vocab.FAULT_TYPES`, including `other`).
- Regenerated after each reconciliation run by `av-analysis refresh --root DIR`.
- Red alerts for `WRONG_FILE_MAPPING`, `ANSWER_LEAK`, `OLD_WAVEFORM_CHANGED` with visit IDs.

**Pending (#35):** the allowlist, panel metrics, last-update rule, reading guide,
masking sign-off.
