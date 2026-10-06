# Analysis

Python analysis tooling. Participant records and identifying metadata remain
outside this repository: real data roots live in restricted storage, and only synthetic
(`DEMO-`, watermarked `SYNTHETIC`) data, hashes and small summaries are committed.

Package `av_analysis` (distribution `av-analysis`, Python 3.11, `uv` project; path
dependency on `../schedules`). Architecture, module ownership and rules:
[`docs/architecture.md`](docs/architecture.md). Formats for other components:
[`docs/interfaces/analysis.md`](../docs/interfaces/analysis.md).

| Part | Issue | Status |
| --- | --- | --- |
| Shared contracts: log templates, vocabularies, checks and codes, windows, table specs, schemas, data roots and watermark, masking | skeleton | implemented |
| Reconciliation: loaders, checks C1-C8, exposure ledger, reconciled and derived tables, synthetic logs | #33 | interfaces |
| Analysis pipeline: scoring, estimators, bootstraps, GLMMs (R), bounds, tipping points, simulation, section 9 report | #34 | interfaces (ladder log, report order and R pins implemented) |
| Integrity dashboard: column allowlist, panels, red alerts, static HTML and metrics JSON, synthetic demo tables | #35 | implemented |

## Use

```sh
uv sync --project analysis --locked
uv run --project analysis av-analysis --help
# A synthetic data root (outputs refuse to mix SYNTHETIC and REAL data)
uv run --project analysis av-analysis init-root <dir> --kind SYNTHETIC --label DEMO-local
# Check the committed JSON Schemas (or rewrite them with --write)
uv run --project analysis av-analysis schemas
# Compare the external methodology templates with the encoded headers and hashes
uv run --project analysis av-analysis check-templates <methodology templates folder>
# After each visit: reconcile --all, derive, dashboard (skips steps not implemented yet)
uv run --project analysis av-analysis refresh --root <dir>
# Integrity dashboard on synthetic (DEMO) reconciled tables
uv run --project analysis python -m av_analysis.monitoring_demo --out <dir> --inject wrong_hash
uv run --project analysis av-analysis dashboard --root <dir>
```

## API

| Module | Contents | Issue |
| --- | --- | --- |
| `templates` | `TEMPLATES`, `Template`, `TRIAL_LOG_COLUMNS` (and the other three), `EXTENSION_COLUMNS`, `TEMPLATE_SHA256`, `COLUMN_CLASS`, `columns_of_class()`, `check_external(dir)` | skeleton |
| `vocab` | raw-log values (`PLAYBACK_STATUS`, `AUDIBLE_STATUS`, `RESPONSE_CODES`, ...), `FAULT_TYPES`, `fault_type()`, `split_fault_codes()`, `parse_timestamp()`, output enumerations, thresholds | skeleton |
| `codes` | `CHECKS`, `CODES`, `code()`, `SUSPENSION_EVENTS`, `FAULT_INJECTIONS` | skeleton |
| `windows` | `WINDOWS`, `window()`, `classify()`, `yoked_gap_ok()` | skeleton |
| `derived` | `TRIALS`, `ENDPOINTS`, `VISIT_STATUS`, `DISCREPANCIES`, `EXPOSURE_CUMULATIVE`, `ENROLLMENT`, `table_bytes()`, `parse_table()`, `row_schema()` | skeleton |
| `schemas` | `schema_documents()` (core plus modules' `SCHEMAS`), `check_schema_files()`, `validator(name)` | skeleton |
| `paths` | `DataRoot`, `INPUT_PATHS`, `write_output()`, `write_synthetic_input()`, `remove_synthetic_input()`, `check_watermark()`, `visit_id()`, `WatermarkError` | skeleton |
| `masking` | `forbidden_reason(field, policy)`, `forbidden_columns()`, `forbidden_keys()` | skeleton |
| `fileio`, `seeds` | canonical bytes and hashes; `rng(*labels)` | skeleton |
| `cli` | `av-analysis` commands; `refresh` runs `REFRESH_STEPS` (reconcile, derive, dashboard) | skeleton |
| `loaders`, `references`, `reconcile`, `ledger`, `derive`, `synthetic_logs` | reconciliation | #33 |
| `scoring`, `unmask`, `estimators`, `missingness`, `glmm`, `rbridge`, `simulate`, `report`, `pipeline` | analysis pipeline | #34 |
| `monitoring`, `monitoring_metrics`, `monitoring_html`, `monitoring_demo` | integrity dashboard: `allowlist()`, `load_monitoring_data()`, `render()`, `write_dashboard()`, `build_document()`, `demo_tables()` | #35 |

Schemas: [`schema/`](schema/) (`*-row.schema.json` for the six tables, `data-root`,
`exit-manifest`, `reconciliation`, `outputs-manifest`, `glmm-log`; `dashboard-data` from
`monitoring`). R environment:
[`r/pins.dcf`](r/pins.dcf), `r/install.R`, `r/check_pins.R`.

## Development

```sh
uv run --project analysis ruff check --config analysis/pyproject.toml analysis/src tests/analysis
uv run --project analysis ruff format --config analysis/pyproject.toml --check analysis/src tests/analysis
(cd analysis && uv run mypy)
uv run --project analysis pytest --import-mode=importlib -p no:cacheprovider tests/analysis
```

Set `AV_TEMPLATES_DIR` to the external methodology templates folder (or
`AV_PLANNING_DIR` to the planning-materials folder next to it) to also run the template
drift test. Tests marked `needs_r` run when `Rscript` with the pinned packages is on the
path (or `AV_RSCRIPT` names it); CI runs them in the `r` job.
