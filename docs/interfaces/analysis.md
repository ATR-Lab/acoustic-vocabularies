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
| `reconciled/` | #33 | `<visit_id>/reconciliation.json`, `visit-status.csv`, `discrepancies.csv`, `exposure-cumulative.csv`, `manifest.json` |
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
| `store_snapshot` | `inputs/store-snapshots/{unit_id}/{visit}.json` (Study B, after each wave) |
| `golden_manifest` | `inputs/sound/golden-manifest.json` |
| `generation_audit` | `inputs/generation/{study}-{set}-audit.csv` (**Pending** #24) |
| `book_key` | `keys/A/{set}-book-key.json` |

**Exit manifest** ([schema](../../analysis/schema/exit-manifest.schema.json)), written by
the station when a session closes (**Pending** agreement with #72/#73): `format`
`av-analysis/exit-manifest`, `format_version` 1, `data_kind`, `visit_id`, `session_id`,
`station_id`, `closed` (`complete`/`interrupted`), `files` (`path`, `bytes`, `sha256`
of every other file in the folder).

**Watermark.** JSON outputs carry top-level `data_kind`; CSV outputs a first column
`data_kind`; HTML `<meta name="av-data-kind" content="SYNTHETIC|REAL">`; Markdown a line
`av-data-kind: SYNTHETIC|REAL`; synthetic HTML and Markdown a visible `SYNTHETIC` banner.
`paths.write_output` refuses anything else and any write into a root of the other kind.

### Raw-log values (proposal; **Pending** confirmation by #67, #72, #73)

| Column | Values |
| --- | --- |
| trial log `playback_status` | `played`, `uncertain`, `no_onset`, `no_cue` |
| trial log `response_code` | `commit`, `dont_know`, `timeout`, `none` |
| trial log `technical_fault_code` | empty or `audio_underrun`, `missing_playback`, `hash_mismatch`, `failed_reset`, `missing_response_log`, `presentation_freeze` |
| exposure ledger `audible_status` | `audible`, `uncertain`, `not_audible` |
| run sheet `comfort_check` | `ok`, `adjusted`, `stopped` |
| deviations `category` | `technical`, `audio`, `matching`, `window`, `missed_visit`, `withdrawal`, `comfort`, `corpus_exposure`, `answer_leak`, `procedure`, `correction`, `other` |
| booleans | `true`, `false` |
| `waveform_sha256` | PCM-sample SHA-256 (`composite_sha256` for messages, `pcm_sha256` for atoms and options) |

### Tables

Row schemas (generated from `derived.TABLES`, typed rows):
[`trials`](../../analysis/schema/trials-row.schema.json),
[`endpoints`](../../analysis/schema/endpoints-row.schema.json),
[`visit-status`](../../analysis/schema/visit-status-row.schema.json),
[`discrepancies`](../../analysis/schema/discrepancies-row.schema.json),
[`exposure-cumulative`](../../analysis/schema/exposure-cumulative-row.schema.json).
CSV: UTF-8, `\n`, header = column names in schema order (`required`), first column
`data_kind`, empty cell = null, `true`/`false`, decimal integers, floats as shortest
round-trip decimal, dates `YYYY-MM-DD`, lists joined by `|`, rows sorted and unique on
the table key. Read and write them with `derived.parse_table` / `derived.table_bytes`.

### Checks and discrepancy codes

| Check | Name | Codes (suspension event) |
| --- | --- | --- |
| C1 | raw-integrity | `RAW_MANIFEST_MISSING`, `RAW_FILE_MISSING`, `RAW_FILE_UNLISTED`, `RAW_HASH_CHANGED`, `RAW_FORMAT`, `REFERENCE_INPUT` |
| C2 | counts | `COUNT_MISSING_TRIAL`, `COUNT_EXTRA_TRIAL`, `COUNT_MISSING_PLAY`, `COUNT_EXTRA_PLAY`, `COUNT_RUN_SHEET`, `BLOCK_ORDER` |
| C3 | waveform-hashes | `WAVEFORM_HASH_MISMATCH`, `PACKAGE_HASH_MISMATCH` (both `WRONG_FILE_MAPPING`) |
| C4 | exposure | `HOLDOUT_OUTSIDE_TEST`, `HOLDOUT_WRONG_VISIT`, `HOLDOUT_REPEAT_AS_NOVEL`, `UNCERTAIN_NOT_CONSUMED`, `RETRY_LINK_BROKEN`, `ANSWER_DISPLAY_LEAK` (`ANSWER_LEAK`) |
| C5 | growth (B) | `OLD_ATOM_CHANGED` (`OLD_WAVEFORM_CHANGED`) |
| C6 | yoked-ledger (B) | `YOKED_SOURCE_MISSING`, `YOKED_MISMATCH`, `YOKED_GAP` |
| C7 | windows | `WINDOW_EARLY`, `WINDOW_LATE`, `VISIT_ORDER` |
| C8 | deviation-links | `DEVIATION_MISSING`, `DEVIATION_UNKNOWN` |

`codes.CODES` holds each code's title, description and first resolution step; the list
may grow (consumers render codes generically). Windows (`windows.WINDOWS`): A D7 6-8 days
after D0; B V2 1-3 and V3 3-5 days after V1 (V3 after V2); W1 6-8 and W4 26-30 days after
the person's V3; yoked acquisition sessions after the active one ended and within 24 h of
its start.

### Masking policies

`masking.forbidden_reason(field, policy)`: policy `masked` (reports, reconciled tables,
everything the dashboard reads or renders) forbids outcome, response, response-time,
hidden-answer, rating, condition and personal fields; policy `derived` (derived tables)
forbids condition and personal fields.

### Command line

`av-analysis` (`uv run --project analysis av-analysis ...` or `python -m av_analysis`):
`init-root`, `schemas [--write]`, `check-templates [DIR]` (skeleton); `synth-logs`,
`reconcile`, `derive` (#33); `run`, `simulate` (#34); `dashboard` (#35). Exit codes: 0
success, 1 findings, 2 refused input, 3 not implemented yet.

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
- `av-analysis derive --root DIR`: the five tables and the area manifests.
- `av-analysis synth-logs --demo-seed DEMO-... --out DIR [--fault NAME --visit ID]`:
  SYNTHETIC roots for every visit type; fault injection (`codes.FAULT_INJECTIONS`).

**Pending (#33):** loader value rules, reference-input details, the per-check rules,
how counts in `visit-status` are computed, sample reports per visit type, timings.

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
  synthetic derived tables and operating characteristics.

**Pending (#34):** estimator output tables, report file layout, model specifications,
R scripts, scenario list, operating-characteristics CSV columns.

## Integrity dashboard (#35)

Producer: integrity monitoring dashboard (#35). Consumers: study coordinator and
research assistants (masked staff), pilot reviews. Design and reading guide:
`analysis/docs/monitoring.md`.

- `av-analysis dashboard --root DIR`: `monitoring/index.html` (static, watermarked) from
  `reconciled/visit-status.csv` and `reconciled/discrepancies.csv` only, through an
  explicit column allowlist (`monitoring.allowlist()`); panels `monitoring.PANELS`
  (alerts, enrollment, allocation, attrition, windows, faults, reconciliation).
- Red alerts for `WRONG_FILE_MAPPING`, `ANSWER_LEAK`, `OLD_WAVEFORM_CHANGED` with visit IDs.

**Pending (#35):** the allowlist, panel metrics, last-update rule, reading guide,
masking sign-off.
