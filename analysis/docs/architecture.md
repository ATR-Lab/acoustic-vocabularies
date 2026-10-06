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

The skeleton commit on `o4.5.1-reconcile` holds the project, the shared contracts
(implemented and tested), the interfaces of all three issues (signatures that raise
`NotImplementedError`), this document, the interface document and CI. The three issues
can then be built in parallel without editing a shared file.

## 1. Module map

`analysis/src/av_analysis/`. "Shared" modules are implemented in the skeleton; an issue
changes one only when unavoidable and says why in its pull request.

| Module | Kind | Owner | Contents |
| --- | --- | --- | --- |
| `templates` | shared | skeleton | column lists of the four methodology log templates, reviewed SHA-256, column classes, drift check |
| `vocab` | shared | skeleton | raw-log value vocabularies (proposal), output enumerations, protocol thresholds |
| `codes` | shared | skeleton | checks C1-C8, discrepancy codes, suspension events, fault-injection map |
| `windows` | shared | skeleton | visit windows and the yoked 24 h rule |
| `derived` | shared | skeleton | specifications of the five reconciled and derived tables, cell encoding, row schemas, canonical table bytes |
| `schemas` | shared | skeleton | generation and check of every published JSON Schema |
| `paths` | shared | skeleton | data-root layout, reference input paths, SYNTHETIC/REAL watermark rule |
| `masking` | shared | skeleton | deny lists for outcome, response, hidden-answer, rating, condition and personal fields |
| `fileio` | shared | skeleton | canonical JSON/CSV bytes, strict CSV reading, hashes, atomic writes |
| `seeds` | shared | skeleton | seeded numpy generators from stored labels |
| `cli` | shared | skeleton | `av-analysis` command; issue commands register from their modules |
| `_paths` | shared | skeleton | location of `analysis/schema/` and `analysis/r/` |
| `loaders` | interface | #33 | raw template CSVs with value validation |
| `references` | interface | #33 | frozen reference inputs of a visit |
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

An issue may add modules of its own (for example `reconcile_checks`) and must keep the
signatures above or update their callers in the same pull request; it must not re-export
from `__init__`. Tests live in `tests/analysis/` with globally unique basenames prefixed
by the topic (`test_reconcile*.py`, `test_pipeline*.py`, `test_monitoring*.py`); the
issues' "analysis/tests/..." evidence paths map to this folder. Each issue fills its own
section of [`docs/interfaces/analysis.md`](../../docs/interfaces/analysis.md) and writes
its guide in `analysis/docs/` (`reconciliation.md` with the discrepancy-code guide,
`pipeline.md`, `monitoring.md`).

**Shared files and their rules.** `pyproject.toml` and `uv.lock` predeclare every
third-party dependency (do not edit). `codes.py`: #33 may append codes (never rename or
remove one once a report uses it); #35 renders any code through `codes.code()`.
`derived.py`: #33 may add a nullable column at the end of a table with a matching
`schemas --write` and a note to #34/#35; a renamed or removed column is a contract change
for the orchestrator. `vocab.py` raw-log values are a proposal pending the producers.

## 2. Data flow

```text
 restricted storage (never in git)                    analysis data root (paths.DataRoot)
 ---------------------------------                    ----------------------------------
 station exports at visit exit  ──copy──>  raw/<visit_id>/ 4 template CSVs + exit-manifest.json   (read-only)
 later deviations, corrections  ──append─> raw/deviations-log.csv                                 (read-only)
 schedules, run sheets, lists,  ──copy──>  inputs/   (no method key)                              (read-only)
 package JSON, store snapshots
 Study A book key               ──copy──>  keys/     (#34 only)                                   (read-only)

 #33  av-analysis reconcile  ──> reconciled/<visit_id>/reconciliation.json      (masked)
 #33  av-analysis derive     ──> reconciled/visit-status.csv, discrepancies.csv,
                                 exposure-cumulative.csv                          (masked)
                             ──> derived/trials.csv, endpoints.csv                (outcomes, no conditions)
 #34  av-analysis run        ──> estimates/  section 9 report, tables, GLMM logs   (unmasked here only)
 #35  av-analysis dashboard  ──> monitoring/index.html  from reconciled/ only     (masked)
```

Raw files are opened read-only and hashed before and after each reconciliation; their
SHA-256 values must not change. A derived score is trusted only for a visit whose
reconciliation passes or whose discrepancies are all explained by deviation records.
Every output area gets `<area>/manifest.json` (`outputs-manifest.schema.json`) with the
SHA-256 of its files and inputs; data locks archive those manifests.

## 3. Data roots and the watermark

A data root is a directory with `av-data-root.json` (`data-root.schema.json`) that fixes
its data kind: `SYNTHETIC` (label `DEMO-...`) or `REAL`. Layout, reference input paths
(`paths.INPUT_PATHS`) and raw file names are in `paths`. Rules:

- Outputs go only to `reconciled/`, `derived/`, `estimates/` and `monitoring/` of a root
  whose kind equals the writer's (`paths.write_output`); `raw/`, `inputs/` and `keys/`
  are read-only. Paths cannot leave their area.
- Every output carries the watermark: JSON top-level `data_kind`, CSV first column
  `data_kind`, HTML `<meta name="av-data-kind" content="...">`, Markdown a line
  `av-data-kind: ...`; synthetic HTML and Markdown also show a visible `SYNTHETIC` banner.
- Roots of different kinds never nest. A REAL root may not lie inside a committable path
  of a git work tree. Synthetic generators (#33 `synth-logs`, #34 `simulate`) create
  SYNTHETIC roots only, so synthetic outputs cannot be written into real-data directories
  and real outputs cannot be written into synthetic ones or into this repository.
- Inputs follow the same rule: a REAL root refuses DEMO schedules, packages or seeds and
  raw visits whose exit manifest says `SYNTHETIC` (#33 loaders).

## 4. Tables

`derived.TABLES` specifies five tables (columns, types, nullability, domains, key,
order, masking policy); the row schemas in `analysis/schema/` are generated from them.
Encoding: UTF-8, `\n`, header = column names, empty cell = null, `true`/`false`,
decimal integers, floats as Python `repr`, dates `YYYY-MM-DD`, lists joined by `|`, rows
sorted by the spec's `sort` columns, unique on its `key`. `derived.table_bytes` and
`derived.parse_table` are the only writer and reader.

| Table | Area | Row | Producer -> consumers |
| --- | --- | --- | --- |
| `trials` | derived | trial-log row of a reconciled visit (items, retries) with schedule item, private target, logged response, delivery, exposure history, timing | #33 -> #34 |
| `endpoints` | derived | person x visit x battery: scheduled, accounted, fault and valid-delivery counts, completeness, missing reason, window timing; never a score | #33 -> #34 |
| `visit-status` | reconciled | expected visit of each revealed person: state, reconciliation, checks failed, suspension events, window, pair gap, booking overrun, faults by type, comfort, deviations | #33 -> #35, #34 |
| `discrepancies` | reconciled | discrepancy of a report: check, code, rows, deviation link | #33 -> #35, #34 |
| `exposure-cumulative` | reconciled | person x item: first audible exposure, plays by phase, violations | #33 -> #34 |

Scoring belongs to #34: the endpoint table states denominators and availability, so
reconciliation (#33) never computes accuracy and can run while masked.

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
  and never writes them; Study B pair timing is reported on both members' rows.
- **#35.** Reads only `reconciled/visit-status.csv` and `reconciled/discrepancies.csv`
  through an explicit column allowlist; every allow-listed column must pass policy
  `masked`; free-text template columns are never rendered; a disallowed column fails the
  build. Faults pooled and by station, never by condition. Coded IDs only.
- **#34.** The only place where conditions meet outcomes (`unmask`). Its outputs live in
  `estimates/`, which #35 never reads.
- No names, contact data or payment data anywhere: person slots and coded IDs only
  (Common procedures section 8).

## 6. Determinism and seeds

No wall clock in any output (the dashboard's "last update" is the latest visit date and
the input hashes), no global random state, no network at run time. Random draws use
`seeds.rng(label, ...)` (PCG64 from SHA-256 of the label); every label is written into the
output that used it; synthetic labels start with `DEMO-`. JSON outputs use
`fileio.json_bytes`, hashes use `fileio.canonical_json_bytes` where a compact form is
needed. Ordering never depends on dictionary, set or file-system order.

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
| Package `manifest.json`, `audio.json` (expected PCM hashes, package hash) | sound package builder (#13) | #33 (C3) | `docs/interfaces/package-format.md` (sound stack) |
| Vocabulary-store snapshots `{atom_id: {recipe_sha256, pcm_sha256, profile, semantic_label}}` | sound store (#11), bank selection (#26, #70) | #33 (C5) | `sound/docs/store.md` (sound stack) |
| Golden manifest (renderer version, hash, digests) | sound goldens (#12) | #34 (fidelity section) | `sound/docs/golden.md` (sound stack) |
| Generation fallback flags and effort tables | generation audit (#24) | #34 (non-fallback sensitivity) | **Pending** (#24) |
| Raw logs and exit manifest | data logging (#72), session engine (#67), operator console (#73) | #33 | methodology templates (`templates`), `exit-manifest.schema.json`, `vocab` values: **Pending** agreement |

Fixtures from other stacks are copied from their committed DEMO examples into
`tests/analysis/fixtures/` (for example `sound/examples/package-demo/manifest.json`),
never imported.

## 10. Decisions and open items

- **Templates are the log format.** Loaders accept the methodology template headers
  exactly. The apparatus' provisional exports (ADR-007, `data-csv-provisional-1`) differ
  and need a mapping before real use: **Pending** (#72/#73 with #33); headset-specific
  extension columns are accepted only once listed.
- **Logged waveform hashes are PCM-sample SHA-256 values** (`composite_sha256` for
  messages, `pcm_sha256` for atoms and options), never file hashes: **Pending**
  confirmation by #64/#72.
- **Exit manifest** (`exit-manifest.schema.json`) is written at session close by the
  station; the synthetic generator writes the same file. Producer agreement **Pending**.
- **Deviation links.** A discrepancy is resolved by a trial-log `deviation_id`, an
  exposure-ledger `matching_deviation_id`, or a deviation record whose `event_id` names
  the row, visit or person slot; later corrections go to the append-only
  `raw/deviations-log.csv`.
- **Yoked timing** is read as: the yoked session starts after the active session ended
  and within 24 h of the active start (`windows.yoked_gap_ok`).
- **Ratings and consultation** have no methodology template; #34 reads them from a
  ratings export whose format is **Pending**.
- **Enrollment targets** for #35 are the planning budgets in `av_schedules.planning`
  until the sample-size decisions (O6.4.1, O6.4.3) freeze them; screening cases (Study B)
  come from reveal-log eligibility records: **Pending** (#35 with #33).
