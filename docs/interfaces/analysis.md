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
| extension columns | trial log and exposure ledger may end with `pcm_sha256` (`templates.EXTENSION_COLUMNS`) | proposal |
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
| C2 | counts | `COUNT_MISSING_TRIAL`, `COUNT_EXTRA_TRIAL`, `COUNT_MISSING_PLAY`, `COUNT_EXTRA_PLAY`, `COUNT_RUN_SHEET`, `BLOCK_ORDER` |
| C3 | waveform-hashes | `WAVEFORM_HASH_MISMATCH`, `PACKAGE_HASH_MISMATCH` (both `WRONG_FILE_MAPPING`), `WAVEFORM_HASH_MISSING` |
| C4 | exposure | `HOLDOUT_OUTSIDE_TEST`, `HOLDOUT_WRONG_VISIT`, `HOLDOUT_REPEAT_AS_NOVEL`, `UNCERTAIN_NOT_CONSUMED`, `RETRY_LINK_BROKEN`, `ANSWER_DISPLAY_LEAK` (`ANSWER_LEAK`) |
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
tables), data-lock gates (reports). Design: `analysis/docs/reconciliation.md` (with the
discrepancy-code guide).

- `av-analysis reconcile <visit_id>... --root DIR` (or `--all`): checks C1-C8 per visit,
  `reconciled/<visit_id>/reconciliation.json`
  ([schema](../../analysis/schema/reconciliation.schema.json)): `inputs` (every file read,
  with SHA-256), `raw_unchanged`, `checks` (C1..C8: `status` `pass`, `explained`, `fail`,
  `not_applicable`; counts), `discrepancies` (`seq`, `check`, `code`, `rows`,
  `deviation_id`, `resolved`, `suspension_event`, `detail`), `summary`. Same inputs, same
  bytes. Exit 1 when a visit fails.
- `av-analysis derive --root DIR`: the six tables and the area manifests.
- `av-analysis synth-logs --demo-seed DEMO-... --out DIR [--fault NAME --visit ID]`:
  SYNTHETIC roots for every visit type; fault injection (`codes.FAULT_INJECTIONS`).

Synthetic generators write `raw/` and `inputs/` only through
`paths.write_synthetic_input`; synthetic event IDs are opaque.

**Pending (#33):** loader value rules, reference-input details, the per-check rules,
how counts in `visit-status` and `enrollment` are computed, sample reports per visit
type, timings.

## Analysis pipeline (#34)

Producer: analysis pipeline on synthetic data (#34). Consumers: sample-size decisions
(O6.4.1, O6.4.3), confirmatory analyses (O7.3.2, O8.3.2: frozen pipeline version and
hash). Design: `analysis/docs/pipeline.md`.

- `av-analysis run --study A|B --data DIR`: reads `derived/`, `reconciled/` and `keys/`;
  writes `estimates/` in analysis plan section 9 order (`report.REPORT_SECTIONS`: flow,
  fidelity, A primary, B primary, secondary outcomes, ownership and consultation,
  sensitivities, deviations), every table stating independent units, trials and missing
  denominators (`report.TableMeta`).
- GLMM logs `estimates/glmm/<model_id>.json`
  ([schema](../../analysis/schema/glmm-log.schema.json)): every ladder rung tried or
  skipped in the order full, no_correlations, no_dyad_role_slope,
  no_participant_teaching_slope, descriptive, with converged and singular flags, R and
  lme4 versions.
- R: `analysis/r/pins.dcf` (R 4.6.1, CRAN snapshot 2026-09-01, lme4 2.0-6),
  `install.R`, `check_pins.R`; `rbridge.run_r` contract in `rbridge`.
- `av-analysis simulate --scenario NAME --datasets N --seed DEMO-... --out DIR`:
  synthetic derived tables and operating characteristics; `simulate.simulate_dataset`
  returns a `SyntheticDataset` (tables plus the key and list files `unmask` reads,
  written with `paths.write_synthetic_input`).
- Interfaces fixed by the skeleton: `unmask.load_conditions(root, study, set) ->
  Conditions` (books or dyads, person -> unit and condition, unit -> planned persons);
  `missingness.all_assigned_bounds(study, scores, conditions, planned)` and
  `tipping_grid(study, scores, conditions, planned, *, step=0.05)` over
  `scoring.BatteryScore` values (with `operational_sum`, the known contribution of a
  partial battery).

**Pending (#34):** estimator output tables, report file layout, model specifications,
R scripts, scenario list, operating-characteristics CSV columns.

## Integrity dashboard (#35)

Producer: integrity monitoring dashboard (#35). Consumers: study coordinator and
research assistants (masked staff), pilot reviews O6.3.4 and O6.3.5 (fault and overrun
rates). Design, allowlist and reading guide: `analysis/docs/monitoring.md`.

- `av-analysis dashboard --root DIR` reads `reconciled/visit-status.csv`,
  `reconciled/discrepancies.csv` and `reconciled/enrollment.csv` only and writes
  `monitoring/index.html` (static page: inline CSS, no script, no network),
  `monitoring/dashboard.json` (panel metrics,
  [schema](../../analysis/schema/dashboard-data.schema.json)) and
  `monitoring/manifest.json` (`outputs-manifest.schema.json`). Exit 0 written, 1 written
  with red alerts, 2 refused input (nothing written). `av-analysis refresh --root DIR`
  regenerates it after each reconciliation run.
- **Column allowlist** (`monitoring.ALLOWED_COLUMNS`, `allowlist()`): every column of
  the three tables is either allow-listed or a reviewed exclusion
  (`monitoring.EXCLUDED_COLUMNS`, dropped at load). A column that `masking` forbids
  under policy `masked`, a free-text template column or any other column fails the
  build before a row is read. Contract for #33: a column added to one of these tables
  makes the dashboard refuse it until #35 classifies the column.
- **Panels** (`monitoring.PANELS`): suspension alerts, enrollment against frozen targets
  (`monitoring_metrics.TARGETS`: Study A 216 learners in 54 books and 18 batches, pilot 18
  in 9 and 3; Study B 128 participants in 64 dyads, pilot 16 in 8; planning budgets until
  O6.4.1 and O6.4.3 freeze them), allocation progress per batch or dyad (Study B
  members counted per dyad, never which member), attrition and missed visits, window
  adherence and dyad pair timing, faults pooled, per study and set and per station by
  `vocab.FAULT_TYPES` against the 5% trigger, overruns against the 10% trigger,
  reconciliation status, discrepancy codes, open deviations, comfort and withdrawal
  reports. No per-person, per-book or per-condition split.
- **Red alerts** for `WRONG_FILE_MAPPING`, `ANSWER_LEAK`, `OLD_WAVEFORM_CHANGED`: from a
  discrepancy's `suspension_event` (or its code's event) and from `visit-status`
  `suspension_events`, with the affected visit IDs; a linked deviation record does not
  remove an alert. Amber triggers: `monitoring_metrics.TRIGGERS` (rates strictly above
  5% and 10%; `enrollment_mismatch` also when a study and set has `visit-status` rows
  but no `enrollment` row).
- **Last update**: the later of the latest visit date and the latest reveal-log date,
  with the SHA-256 of the three input tables (no wall clock).
- `dashboard.json` (`format` `av-analysis/dashboard-data`, `format_version` 1): `as_of`,
  `inputs`, `thresholds`, `groups`, `enrollment`, `allocation`, `attrition`, `windows`,
  `faults` (`pooled`, `by_group`, `by_station`: `opportunities_n`, `fault_n`,
  `fault_rate`, `trigger_exceeded`, `by_type`), `overruns` (`pooled`, `by_group`,
  `by_visit`, `by_station`: `checked_n`, `overrun_n`, `overrun_share`,
  `trigger_exceeded`), `reconciliation`, `alerts` (`suspension`, `triggers`). Strict
  at every level; counts, coded IDs, enumerations and generated text only.
- Synthetic demonstration tables: `python -m av_analysis.monitoring_demo --out DIR
  [--seed DEMO-...] [--study] [--set] [--progress] [--inject NAME[=VISIT_ID]]`
  (SYNTHETIC roots only; stand-in until #33's `synth-logs` and `derive` run in the chain).

**Pending (#35):** advisor sign-off on masking (human); frozen targets (O6.4.1,
O6.4.3); screening-case source (#73 with #33).
