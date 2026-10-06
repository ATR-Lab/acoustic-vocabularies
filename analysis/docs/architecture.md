# Analysis architecture (O4.5: #33, #34, #35)

This document fixes how the analysis stack is split, which contracts its three issues
share, and the rules every part follows. Formats for other components are in
[`docs/interfaces/analysis.md`](../../docs/interfaces/analysis.md). Protocol references:
Common procedures sections 7-8, Study A protocol section 8, Study B protocol sections 5,
10 and 11, analysis plan sections 2-9.

| Issue | WBS | Branch | Builds |
| --- | --- | --- | --- |
| #33 | O4.5.1 | `o4.5.1-reconcile` | reconciliation checks C1-C8, cumulative exposure ledger, reconciled and derived tables, synthetic raw logs, fault injection |
| #34 | O4.5.2 | `o4.5.2-analysis-pipeline` | scoring, section 3-6 estimators, bootstraps, GLMMs with the fallback ladder, bounds, tipping points, synthetic datasets, the section 9 report |
| #35 | O4.5.3 | `o4.5.3-integrity-dashboard` | the masked integrity dashboard |

The skeleton commits on `o4.5.1-reconcile` hold the project, the shared contracts
(implemented and tested), the interfaces of all three issues (signatures that raise
`NotImplementedError`), this document, the interface document and CI. The three issues
can then be built in parallel without editing a shared file: each issue documents its
own modules in its own guide, publishes its own schemas from its own modules (`SCHEMAS`),
adds its own CI evidence script (`analysis/ci/<issue>.sh`) and fills its own section of
the interface document.

## 1. Module map

`analysis/src/av_analysis/`. "Shared" modules are implemented in the skeleton; an issue
changes one only when unavoidable and says why in its pull request.

| Module | Kind | Owner | Contents |
| --- | --- | --- | --- |
| `templates` | shared | skeleton | column lists of the four methodology log templates, extension columns, reviewed SHA-256, column classes, drift check |
| `vocab` | shared | skeleton | raw-log values (aligned with the provisional producer), fault types, times, output enumerations, protocol thresholds |
| `codes` | shared | skeleton | checks C1-C8, discrepancy codes, suspension events, fault-injection map |
| `windows` | shared | skeleton | visit windows and the yoked 24 h rule (aware times) |
| `derived` | shared | skeleton | specifications of the six reconciled and derived tables, cell encoding, row schemas, canonical table bytes |
| `schemas` | shared | skeleton | generation and check of every published JSON Schema, including the modules' `SCHEMAS` |
| `paths` | shared | skeleton | data-root layout, reference input paths, SYNTHETIC/REAL watermark rule, the synthetic-input writer |
| `masking` | shared | skeleton | deny lists for outcome, response, hidden-answer, rating, condition, personal and staff fields |
| `fileio` | shared | skeleton | canonical JSON/CSV bytes, strict CSV reading, hashes, atomic writes |
| `seeds` | shared | skeleton | seeded numpy generators from stored labels |
| `cli` | shared | skeleton | `av-analysis` command and `refresh`; issue commands register from their modules |
| `_paths` | shared | skeleton | location of `analysis/schema/` and `analysis/r/` |
| `loaders` | interface | #33 | raw template CSVs with value validation |
| `references` | interface | #33 | frozen reference inputs of a visit; `ExpectedHash` (implemented) |
| `reconcile` | interface | #33 | checks C1-C8, `reconciliation.json`, `reconcile` command |
| `ledger` | interface | #33 | cumulative per-person exposure ledger |
| `derive` | interface | #33 | reconciled and derived tables, `derive` command |
| `synthetic_logs` | interface | #33 | synthetic raw logs for every visit type, fault injection, `synth-logs` command |
| `scoring` | interface | #34 | Y, valid-delivery score, battery scores, censored time to commit |
| `unmask` | interface | #34 | condition labels from `keys/` and the dyad list (the only reader of `keys/`) |
| `estimators` | interface | #34 | t tests and intervals, Holm, stratified bootstraps |
| `missingness` | interface | #34 | all-assigned bounds, tipping-point grid |
| `glmm` | partly shared | #34 | ladder order and log format (implemented); model specs and fits (interface) |
| `rbridge` | partly shared | #34 | pinned R environment and `Rscript` lookup (implemented); the call (interface) |
| `simulate` | interface | #34 | synthetic datasets, null and alternative scenarios, `simulate` command |
| `report` | partly shared | #34 | section 9 order and table statements (implemented); report building (interface) |
| `pipeline` | interface | #34 | `run` command |
| `monitoring` | partly shared | #35 | panel list (implemented); allowlist, loader, renderer, `dashboard` command (interface) |

An issue may add modules of its own (for example `reconcile_checks`): it documents them
(backticked name) in its own guide, `analysis/docs/reconciliation.md`, `pipeline.md` or
`monitoring.md`; the skeleton test accepts a module named in any `analysis/docs/*.md`. It
must keep the signatures above or update their callers in the same pull request, and it
must not re-export from `__init__`. Tests live in `tests/analysis/` with globally unique
basenames prefixed by the topic (`test_reconcile*.py`, `test_pipeline*.py`,
`test_monitoring*.py`; `test_analysis_*.py` belong to the skeleton); the issues'
"analysis/tests/..." evidence paths map to this folder. Each issue fills its own section
of [`docs/interfaces/analysis.md`](../../docs/interfaces/analysis.md).

**Shared files and their rules.** `pyproject.toml` and `uv.lock` predeclare every
third-party dependency (do not edit). `codes.py`: #33 may append codes (never rename or
remove one once a report uses it); #35 renders any code through `codes.code()`.
`derived.py`: #33 may add a nullable column at the end of a table with a matching
`schemas --write` and a note to #34/#35 (a string column needs an `example`); a renamed or
removed column is a contract change for the orchestrator. `schemas.py`: never edited by
an issue; a module publishes a schema by defining `SCHEMAS = {"<name>.schema.json":
factory}`. `.github/workflows/analysis.yml`: never edited by an issue; CI evidence comes
from `analysis/ci/<issue>.sh` (section 11). `vocab.py` raw-log values are pending the
producers.

## 2. Data flow

```text
 restricted storage (never in git)                    analysis data root (paths.DataRoot)
 ---------------------------------                    ----------------------------------
 station exports at visit exit  ──import─> raw/<visit_id>/ 4 template CSVs + exit-manifest.json   (read-only)
 later deviations, corrections  ──append─> raw/deviations-log.csv                                 (read-only)
 schedules, run sheets, lists,  ──copy──>  inputs/   (no method key)                              (read-only)
 package JSON, reveal log,
 store snapshots and receipts
 Study A book key               ──copy──>  keys/     (#34 only)                                   (read-only)

 #33  av-analysis reconcile  ──> reconciled/<visit_id>/reconciliation.json      (masked)
 #33  av-analysis derive     ──> reconciled/visit-status.csv, discrepancies.csv,
                                 exposure-cumulative.csv, enrollment.csv          (masked)
                             ──> derived/trials.csv, endpoints.csv                (outcomes, no conditions)
 #35  av-analysis dashboard  ──> monitoring/index.html  from reconciled/ only     (masked)
      av-analysis refresh    =   reconcile --all, derive, dashboard (operator sequence after a visit)
 #34  av-analysis run        ──> estimates/  section 9 report, tables, GLMM logs   (unmasked here only)
```

Raw files are opened read-only and hashed before and after each reconciliation; their
SHA-256 values must not change. A derived score is trusted only for a visit whose
reconciliation passes or whose discrepancies are all explained by deviation records.
Every output area gets `<area>/manifest.json` (`outputs-manifest.schema.json`) with the
SHA-256 of its files and inputs; data locks archive those manifests.

The operator runs `av-analysis refresh --root DIR` after each visit is imported: it runs
`reconcile --all`, `derive` and `dashboard` in order (`cli.REFRESH_STEPS`), continues
after findings so the tables and the dashboard show them, stops at a refused input, and
skips a step whose command is not implemented yet. No issue command imports another
issue's module for this.

## 3. Data roots and the watermark

A data root is a directory with `av-data-root.json` (`data-root.schema.json`) that fixes
its data kind: `SYNTHETIC` (label `DEMO-...`) or `REAL`. Layout, reference input paths
(`paths.INPUT_PATHS`) and raw file names are in `paths`. Rules:

- Outputs go only to `reconciled/`, `derived/`, `estimates/` and `monitoring/` of a root
  whose kind equals the writer's (`paths.write_output`). Paths cannot leave their area.
- `raw/`, `inputs/` and `keys/` are read-only for every command. Real exports and frozen
  inputs are copied in by the import step. The only code that writes these areas is
  `paths.write_synthetic_input` (and `paths.remove_synthetic_input` for fault
  injection), used only by the synthetic generators (#33 `synth-logs` and fault
  injection, #34 `simulate`): it accepts SYNTHETIC roots only,
  keeps `raw/` paths to `deviations-log.csv` and `<visit_id>/<file>`, and refuses JSON
  marked as real (`data_kind` other than `SYNTHETIC`, `demo` not `true`), exit manifests
  without `data_kind` `SYNTHETIC`, and CSVs whose `data_kind` column holds another value.
  Generators never write these areas with `fileio` directly.
- Every output carries the watermark: JSON top-level `data_kind`, CSV first column
  `data_kind`, HTML `<meta name="av-data-kind" content="...">`, Markdown a line
  `av-data-kind: ...`; synthetic HTML and Markdown also show a visible `SYNTHETIC` banner.
- Roots of different kinds never nest. A REAL root may not lie inside a committable path
  of a git work tree. Synthetic generators create SYNTHETIC roots only, so synthetic
  outputs cannot be written into real-data directories and real outputs cannot be
  written into synthetic ones or into this repository.
- Inputs follow the same rule: a REAL root refuses DEMO schedules, packages or seeds, store
  snapshots with `source_kind` `synthetic`, and raw visits whose exit manifest says
  `SYNTHETIC` or has no export `source` (#33 loaders).

## 4. Tables

`derived.TABLES` specifies six tables (columns, types, nullability, domains, examples,
key, order, masking policy); the row schemas in `analysis/schema/` are generated from
them. Encoding: UTF-8, `\n`, header = column names, empty cell = null, `true`/`false`,
decimal integers, floats as Python `repr`, dates `YYYY-MM-DD`, lists joined by `|`, rows
sorted by the spec's `sort` columns, unique on its `key`. `derived.table_bytes` and
`derived.parse_table` are the only writer and reader.

| Table | Area | Row | Producer -> consumers |
| --- | --- | --- | --- |
| `trials` | derived | trial-log row of a reconciled visit (items, retries), or a scheduled opportunity lost to a verified apparatus or logger failure (`row_source` `deviation`); schedule item, private target, logged response, delivery, onset and offset, fault codes and types, exposure history, timing | #33 -> #34 |
| `endpoints` | derived | person x visit x battery: scheduled, accounted, fault, lost and valid-delivery counts, completeness, missing reason, window timing; never a score | #33 -> #34 |
| `visit-status` | reconciled | expected visit of each revealed person: state, reconciliation, checks failed, suspension events, window, pair gap, booking overrun, faults by type, comfort, deviations (all, open, comfort, withdrawal) | #33 -> #35, #34 |
| `discrepancies` | reconciled | discrepancy of a report: check, code, rows, deviation link | #33 -> #35, #34 |
| `exposure-cumulative` | reconciled | person x item: first exposure that consumed it, plays by phase, violations | #33 -> #34 |
| `enrollment` | reconciled | study x set: planned units and persons, eligibility records, screening cases (Pending source), reveals, spares, bank-unavailable records | #33 -> #35, #34 |

Scoring belongs to #34: the endpoint table states denominators and availability, so
reconciliation (#33) never computes accuracy and can run while masked.

**Technical failures versus withdrawal** (analysis plan section 2; Study B protocol
section 10; Common procedures section 8). A scheduled opportunity is *accounted* when it
has a `trials` row: a logged row (played, or a logged technical failure) or a
`row_source` `deviation` row for an opportunity lost to an apparatus or logger failure
that a deviation record verifies (fault code `OPPORTUNITY_LOST`, `valid_delivery` false,
response fields null). Both count in `accounted_n` and `fault_n` and score 0 in the
operational score; the valid-delivery score excludes them. An opportunity never
undertaken because consent was withdrawn has no row: the battery is `partial` or
`missing` with `missing_reason` `withdrawn_mid_battery`. `technical_stop` is used only when
a battery stopped on a technical failure that no deviation record verifies.

## 5. Masking

Analysis plan section 8: during collection, monitor only engineering integrity,
scheduling/retention and welfare; no outcome by condition outside the analysis pipeline.

- **#33 outputs.** Reports and reconciled tables follow policy `masked`
  (`masking.forbidden_reason`): no accuracy, response, response-time, rating,
  hidden-answer, condition or personal field. Discrepancy details name rows and rules,
  never scores. Derived tables follow policy `derived`: outcomes but no method, role or
  scaffold label (no `method_masked`, `role`, `scaffold_family`, `presentation`,
  `yoked_source_event_id`).
- **#33 inputs.** Never reads `keys/`. Uses Study B roles only to match yoked ledgers (C6)
  and never writes them.
- **C6 is symmetric.** C6 is evaluated once per dyad and visit over both members' ledgers;
  the same C6 status, discrepancies and partner input files go into both members'
  reports, so `checks_failed`, `discrepancies.csv` and the dashboard cannot show which
  member is yoked. C6 rows list event IDs sorted, never marked as source or copy; details
  name no role; synthetic event IDs are opaque. Study B pair timing is likewise reported
  on both members' rows.
- **#35.** Reads only `reconciled/visit-status.csv`, `reconciled/discrepancies.csv` and
  `reconciled/enrollment.csv` through an explicit column allowlist; every allow-listed
  column must pass policy `masked`; free-text template columns are never rendered; a
  disallowed column fails the build. Faults pooled and by station, never by condition.
  Coded IDs only.
- **#34.** The only place where conditions meet outcomes (`unmask`). Its outputs live in
  `estimates/`, which #35 never reads.
- **No names.** No names, contact data or payment data anywhere: person slots and coded
  IDs only (Common procedures section 8). The staff template columns (`operator`,
  `reviewer`, `operator_signoff`) hold coded staff IDs (`vocab.STAFF_ID_RE`) and are
  forbidden in every output (class `staff`, reason `personal`).

## 6. Determinism, times and seeds

No wall clock in any output (the dashboard's "last update" is the latest visit date and
the input hashes), no global random state, no network at run time. Random draws use
`seeds.rng(label, ...)` (PCG64 from SHA-256 of the label); every label is written into the
output that used it; synthetic labels start with `DEMO-`. JSON outputs use
`fileio.json_bytes`, hashes use `fileio.canonical_json_bytes` where a compact form is
needed. Ordering never depends on dictionary, set or file-system order.

Raw times (run-sheet `start_time`/`end_time`, deviations `timestamp`) are ISO 8601 with
seconds and a UTC offset (`2027-03-01T09:30:00+01:00`; `vocab.parse_timestamp`). Naive
times are refused (`RAW_FORMAT`). Durations, pair gaps and the yoked 24 h rule use aware
times (`windows` refuses naive datetimes), so a gap across a daylight-saving change is
exact; a visit date is the calendar date in the recorded offset.

## 7. R

R is used only for the supporting GLMMs (`lme4::glmer`), because the fallback ladder
depends on lme4's convergence and singularity checks (issue #34 proposal, adopted).
Python orchestrates: `rbridge.run_r` writes the model data and a request JSON to a fresh
temporary folder and runs `Rscript --vanilla analysis/r/<script>.R`; no rpy2, no R
session state, no network. `analysis/r/pins.dcf` pins the R version, a dated Posit
Package Manager CRAN snapshot and the package versions; `install.R` installs them into
`R_LIBS_USER` and `check_pins.R` verifies them. Each GLMM log records the R and lme4
versions it ran with. Tests that need R are marked `needs_r`: skipped where `Rscript` is
missing, run by the `r` job of `.github/workflows/analysis.yml` (`r-lib/actions/setup-r`,
pinned R, then `install.R`). Nothing is installed system-wide on developer machines; a
local R, if needed, lives in an isolated environment.

## 8. Dependencies

Runtime: `av-schedules` (path dependency on `../schedules`, same stack: imported for
the matrix, visit plans, schedules and allocation helpers), `numpy`, `scipy`,
`jsonschema`. Dev: `pytest`, `pytest-cov`, `hypothesis`, `ruff`, `mypy`, `scipy-stubs`,
`types-jsonschema`. **No pandas**: about 40,000 trial rows per study fit the stdlib `csv`
module and typed rows; explicit parsing keeps types, empty cells and sort order exact on
every platform and keeps mypy strict; numpy and scipy cover the vectorised bootstraps,
simulations and distributions. No plotting library: the report is Markdown with CSV
tables, the dashboard static HTML and CSS.

## 9. Cross-stack inputs (file contracts only)

| Input | Producer | Used by | Contract |
| --- | --- | --- | --- |
| Visit schedules, run sheets, slot and dyad lists, reveal log, package-hash mappings | schedules (#30-#32, same stack) | #33, #34 | [`docs/interfaces/schedules.md`](../../docs/interfaces/schedules.md) |
| Package `manifest.json`, `audio.json` (per item: PCM or composite hash and file hash, `null` for composed audio; package hash) | sound package builder (#13) | #33 (C3) | `docs/interfaces/package-format.md` sections 3 and 5 |
| Study B store snapshots: the menu-store bridge's `verified_snapshot` after each visit (entries `atom_id`, `profile`, `rank`, `pcm_sha256`, `file_sha256`, `selection_receipt_sha256`; `book_head`, `snapshot_sha256`) and its selection receipts (`before_head`/`after_head`) | menu-store bridge (#70) over the sound store (#11) | #33 (C5) | `docs/interfaces/menu-store-bridge.md`; per-atom recipe hashes **Pending** (#70, #26) |
| Golden manifest (renderer version, hash, digests) | sound goldens (#12) | #34 (fidelity section) | `sound/docs/golden.md` (sound stack) |
| Generation fallback flags and effort tables | generation audit (#24) | #34 (non-fallback sensitivity) | **Pending** (#24) |
| Raw logs and their export manifest | data logging (#72), session engine (#67), operator console (#73) | #33 | template headers (`templates`), values (`vocab`, aligned with `data-csv-provisional-1`), exit manifest as a projection of the ExportBundle manifest (`exit-manifest.schema.json`): column adapter and agreement **Pending** |

Fixtures from other stacks are copied from their committed DEMO examples into
`tests/analysis/fixtures/` (for example `sound/examples/package-demo/manifest.json`),
never imported.

**Prior art on main: `tools/mock_visit`.** The mock-visit tools verify retained native
evidence of SIMULATION_TEST visits for engineering-pilot check 8 (Common procedures
section 2): the data journal's hash chain, the ExportBundle manifest and its
provisional CSVs, operator and menu journals, and fault-evidence plans (scenarios
`headset_disconnect`, `input_loss`, `presentation_stall`, `audio_underrun`,
`corrupt_file_hash`, `failed_reset`, `missing_response_log`). #33 does not replace them:
they qualify the station and its export before data reach a data root, and #33 starts
from an imported, hash-pinned export. #33 adapts to them: it uses the same per-visit
block counts (both follow `av_schedules`), accepts the same producer values (`vocab`),
and its exit manifest binds the same ExportBundle manifest hash (`source`). Its own fault
injections (`codes.FAULT_INJECTIONS`) are protocol faults in reconciled logs, not native
apparatus faults.

## 10. Decisions and open items

- **Templates are the log format; values follow the producer.** Loaders accept the
  methodology template headers exactly, plus the listed extension columns
  (`templates.EXTENSION_COLUMNS`: `pcm_sha256`). The provisional exports on main (ADR-007,
  `data-csv-provisional-1`) use other headers; their values (`playback_status`,
  `audible_status`, `response_code`, `;`-joined uppercase `technical_fault_code`) are
  adopted in `vocab`, so the remaining adapter maps columns only: **Pending** (#72/#73
  with #33).
- **Fault types.** Fault codes form an open producer set; `vocab.fault_type` maps them to
  the six protocol fault types or `other` (canonical codes plus known producer codes;
  mapping **Pending** #64/#67/#72). `presentation_freeze` also follows from
  `frame_freeze_ms` > 250 and `failed_reset` from `reset_ok` = `false`.
- **Waveform hashes.** A logged `waveform_sha256` is the file hash for file playback (the
  producer's export) and the PCM/composite hash for audio composed in memory. C3 matches a
  logged hash against both expected hashes of the item (`references.ExpectedHash`); an
  empty hash is accepted only for composed audio with a `pcm_sha256` extension value:
  **Pending** (#64/#72).
- **Exit manifest** (`exit-manifest.schema.json`) is a documented projection of the #72
  ExportBundle manifest (`source`: format, export ID, manifest hash, protocol version,
  build hash, header qualification, torn tail, record and row counts), written when an
  export is imported; the synthetic generator writes `source` null. Agreement **Pending**.
- **Lost opportunities** are accounted `trials` rows with `row_source` `deviation`
  (section 4), not missing data.
- **Deviation links.** A discrepancy is resolved by a trial-log `deviation_id`, an
  exposure-ledger `matching_deviation_id`, or a deviation record whose `event_id` names
  the row, visit or person slot; later corrections go to the append-only
  `raw/deviations-log.csv`.
- **Times** carry a UTC offset (section 6).
- **Yoked timing** is read as: the yoked session starts after the active session ended
  and within 24 h of the active start (`windows.yoked_gap_ok`).
- **C5** is specified against `verified_snapshot` and the bridge's receipts
  (`references`); per-atom recipe identity is **Pending** (#70, #26).
- **Ratings and consultation** have no methodology template; #34 reads them from a
  ratings export whose format is **Pending**.
- **Enrollment.** `enrollment` counts eligibility records and reveals from the reveal
  log; screening cases (Study B people who cannot form a compatible pair) have no source
  yet, so `screening_cases_n` stays null: **Pending** (#73 with #33). Targets are the
  planning budgets in `av_schedules.planning` until the sample-size decisions (O6.4.1,
  O6.4.3) freeze them.

## 11. CI

`.github/workflows/analysis.yml` runs on pull requests touching the analysis stack, on
pushes to `main` and on pushes to the `o4.5.*` branches (so the stacked branches run
before their pull requests open). Job `test` (Ubuntu, macOS, Windows): locked install,
ruff, mypy, schema check, tests without R with a 90% coverage floor, then the **issue
evidence hooks**: every `analysis/ci/*.sh` script runs in name order with bash, with
`AV_CI_OUT` set to `analysis/out/ci/<script name>/` (git-ignored), and
`analysis/out/ci/` is uploaded as the artifact `analysis-evidence-<os>` even when a step
fails. Each issue adds only its own script (`33.sh`: synthetic roots, sample
reconciliation reports per visit type, fault-injection suite; `34.sh`: synthetic section 9
report, null operating characteristics; `35.sh`: synthetic dashboard and its
screenshot); a script that should run on one runner checks `RUNNER_OS` itself. Job `r`
(Ubuntu): pinned R and packages, then the `needs_r` tests.
