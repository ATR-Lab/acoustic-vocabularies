# Schedules

Python scheduling tooling and synthetic JSON fixtures. Allocation lists and
confirmatory seeds remain private: generators live here, their real outputs are written
to the git-ignored `schedules/out/` and kept in restricted storage; only hashes and
clearly labelled DEMO examples are committed.

Package `av_schedules` (distribution `av-schedules`, Python 3.11, `uv` project).
Formats for other components: [`docs/interfaces/schedules.md`](../docs/interfaces/schedules.md).
Design and balance argument: [`docs/curriculum.md`](docs/curriculum.md).
Visit schedules (lesson and trial orders): [`docs/orders.md`](docs/orders.md).

## Use

```sh
uv sync --project schedules --locked
# DEMO run (public seed, outputs are examples only)
uv run --project schedules python -m av_schedules curriculum --demo-seed DEMO-local
# Real pilot or confirmatory run: one private seed file per set, kept outside the repo
# (64+ lowercase hex digits, e.g. python -c "import secrets; print(secrets.token_hex(32))")
uv run --project schedules python -m av_schedules curriculum \
    --master-seed-file <path outside the repo> --set confirmatory
# Per-person visit schedules (hidden answers) and the frozen speech list
uv run --project schedules python -m av_schedules schedules --demo-seed DEMO-local
# Compare the external planning materials with the matrix constant
uv run --project schedules python -m av_schedules check-planning <planning-materials dir>
# Regenerate the committed DEMO examples
uv run --project schedules python -m av_schedules demo-examples
```

Options of `curriculum`: `--study A|B|both`, `--set pilot|confirmatory|both` (`both` only
with `--demo-seed`), `--spares N` (Study B spare slots, multiple of 4 up to 96, default 8),
`--out DIR` (default `schedules/out`), `--force` (overwrite a set written from another
seed). Of the outputs, only `<unit>/permutation.json` is package-safe; `curriculum.csv`,
the batch/design tables, balance reports and manifests of real sets are restricted.
`schedules` takes the same options; all of its outputs for real sets are restricted
(visit schedules are hidden-answer material).

## API

| Module | Function or constant | Purpose | Issue |
| --- | --- | --- | --- |
| `matrix` | `MATRIX`, `LABELS`, `cells()`, `trained_cells(wave)`, `heldout_cells(set)`, `index_waves()`, `wave_atoms(wave)`, `novel_visit(study, set, swap)` | the abstract matrix and everything derived from it | #29 |
| `seeds` | `demo_seed()`, `load_master_seed()`, `derive_seed(master, *parts)`, `SeedStream` | master seeds, SHA-256 derivation, portable integer stream | #29 |
| `design` | `build_a_batch_table(master, set_name)`, `build_b_design_table(master, set_name, *, spares=8)`, `build_units(...)`, `Unit`, `ABatch`, `BDyadSlot`, `Permutation` | Study A batch table and Study B design table with permutations, swap flags and atom orders | #29 |
| `curriculum` | `curriculum_rows(unit)`, `curriculum_csv(unit)`, `permutation_document(unit)`, `permutation_json(unit)`, `novel_by_visit(unit)` | per-unit restricted `curriculum.csv` and package-safe `permutation.json` | #29 |
| `balance` | `balance_rows(units)`, `balance_csv(rows)`, `max_abs_deviation(rows)` | balance report | #29 |
| `output` | `generate(master, study, set_name)`, `render_set(units)`, `write_files(root, files)`, `demo_example_files()` | all files of a set, manifest | #29 |
| `planning` | `check_planning(dir)`, `DESIGN_CHECKS`, `GROWTH_COUNTS`, `PLANNING_SHA256`, `synthetic_planning_files()` | planning-material check and test oracles | #29 |
| `planning` | `ASSESSMENT_SCHEDULE`, `ASSESSMENT_COLUMNS`, `SCHEDULE_CHECKS` | `assessment-schedule.csv` and schedule `design-checks.json` oracles | #30 |
| `orders` | `person_ids(unit)`, `study_visits(study)`, `visit_plan(study, visit)`, `BlockPlan`, `assessment_counts(study, visit)` | person slots and expected block structure per visit | #30 |
| `orders` | `build_visit_schedule(master, unit, person_id, visit)`, `unit_schedules(master, unit)`, `block_order(...)`, `visit_schedule_json(doc)`, `check_visit_schedule(doc)` | per-person visit schedules and their rule check | #30 |
| `orders` | `seed_tokens(...)` (for `derive_seed`), `pass_orders`, `alternating_passes`, `sample_with_replacement` | seed tokens and order primitives | #30 |
| `orders` | `speech_commands(master, study, set)`, `speech_list_document(...)`, `SpeechCommand` | frozen speech list for the validity block | #30 |
| `schedule_output` | `generate_schedules(master, study, set)`, `render_schedules(units, master)`, `schedule_example_files(seed)` | schedule files of a set, summary, manifest | #30 |

Schemas: [`schema/permutation.schema.json`](schema/permutation.schema.json),
[`schema/curriculum-unit.schema.json`](schema/curriculum-unit.schema.json),
[`schema/visit-schedule.schema.json`](schema/visit-schedule.schema.json) (hidden-answer
material), [`schema/speech-list.schema.json`](schema/speech-list.schema.json).

## Development

```sh
uv run --project schedules ruff check --config schedules/pyproject.toml schedules tests/schedules
uv run --project schedules ruff format --config schedules/pyproject.toml --check schedules tests/schedules
(cd schedules && uv run mypy)
uv run --project schedules pytest --import-mode=importlib -p no:cacheprovider tests/schedules
```

Set `AV_PLANNING_DIR` to the external planning-materials folder to also run the two
tests that compare it with the matrix constant; they are skipped otherwise.
