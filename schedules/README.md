# Schedules

Python scheduling tooling and synthetic JSON fixtures. Allocation lists and
confirmatory seeds remain private: generators live here, their real outputs are written
to the git-ignored `schedules/out/` and kept in restricted storage; only hashes and
clearly labelled DEMO examples are committed.

Package `av_schedules` (distribution `av-schedules`, Python 3.11, `uv` project).
Formats for other components: [`docs/interfaces/schedules.md`](../docs/interfaces/schedules.md).
Design and balance argument: [`docs/curriculum.md`](docs/curriculum.md).
Visit schedules (lesson and trial orders): [`docs/orders.md`](docs/orders.md).
Allocation lists and the reveal-next stub: [`docs/allocation.md`](docs/allocation.md).
Run sheets and the schedule validation suite: [`docs/run-sheets.md`](docs/run-sheets.md).

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
# Allocation lists: separate private seed files for pilot and confirmatory (same seed
# as that set's curriculum); output to restricted storage
uv run --project schedules python -m av_schedules allocate \
    --confirmatory-seed-file <path outside the repo> --out <restricted folder>
# Run sheets (checked before writing; real sets to restricted storage) and the
# validation report (exit 1 on any finding)
uv run --project schedules python -m av_schedules run-sheets --demo-seed DEMO-local \
    --set pilot --demo-placeholder-hashes
uv run --project schedules python -m av_schedules check --demo-seed DEMO-local
# Regenerate the committed DEMO run sheets
uv run --project schedules python -m av_schedules run-sheet-examples
# Sample-size presets and their derived counts
uv run --project schedules python -m av_schedules presets
```

Options of `curriculum`: `--study A|B|both`, `--set pilot|confirmatory|both` (`both` only
with `--demo-seed`), `--preset NAME` (sample-size preset, default `provisional-18-64`; see
below), `--spares N` (Study B spare slots, multiple of 4 up to 96, default: the preset's),
`--out DIR` (default `schedules/out`), `--force` (overwrite a set written from another
seed). Of the outputs, only `<unit>/permutation.json` is package-safe; `curriculum.csv`,
the batch/design tables, balance reports and manifests of real sets are restricted.
`schedules` takes the same options; all of its outputs for real sets are restricted
(visit schedules are hidden-answer material). `allocate`, `run-sheets` and `check` also
take `--preset` and `--spares`; use the same values for every command of one set.

## Sample-size presets

The confirmatory unit counts are a configuration choice (#266). The single source is
[`presets/sample-size-presets.json`](presets/sample-size-presets.json) (schema
[`schema/sample-size-presets.schema.json`](schema/sample-size-presets.schema.json)); every
generator takes a preset name, so the chosen counts need no code change. Which preset is
used is the study owner's decision (due 20 Nov); the default keeps today's values and
reproduces the committed DEMO examples byte for byte.

| Preset | Study A batches | Study B pairs | Study B spare slots | Learners | People (B) | Banks |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `provisional-18-64` (default) | 18 | 64 | 8 | 216 | 128 | 72 |
| `revised-30-84` | 30 | 84 | 12 | 360 | 168 | 96 |
| `revised-30-88` | 30 | 88 | 12 | 360 | 176 | 100 |

The same in every preset: the pilot sets (Study A 3 batches; Study B 8 pairs, no
spares), 3 profiles, 3 hand designers `D1`..`D3`, 3 methods, 4 learners per book (pilot
2), 3 raters and 4 panel sessions per batch, Study B blocks of 4 (SQ arm x swap) and one
bank per pair or spare slot. `python -m av_schedules presets` prints every preset with
its derived counts (batches, books, learners, raters, panel sessions, pairs, people,
spare slots, banks).

With 30 batches each profile has 10 batches (5 swapped), and the three hand designers
cannot be balanced within a profile: 4/3/3, with each designer leading one profile, so
every designer still makes 10 books (5 swapped). Within each profile x swap cell the
counts are 2/2/1. Recruitment runs in 10 waves of 3 (one batch per profile, three
different designers, 1 or 2 swapped batches). The layout is in
[`docs/curriculum.md`](docs/curriculum.md#3-blocks-cycles-and-grid-columns), the waves in
[`docs/allocation.md`](docs/allocation.md#2-study-a). The 12 spare slots of the revised
presets are a proposal: the smallest multiple of 4 at or above the 12.5% spare share of
64 + 8.

## API

| Module | Function or constant | Purpose | Issue |
| --- | --- | --- | --- |
| `matrix` | `MATRIX`, `LABELS`, `cells()`, `trained_cells(wave)`, `heldout_cells(set)`, `index_waves()`, `wave_atoms(wave)`, `novel_visit(study, set, swap)` | the abstract matrix and everything derived from it | #29 |
| `seeds` | `demo_seed()`, `load_master_seed()`, `derive_seed(master, *parts)`, `SeedStream` | master seeds, SHA-256 derivation, portable integer stream | #29 |
| `design` | `build_a_batch_table(master, set_name, *, batches=None)`, `build_b_design_table(master, set_name, *, spares=8, dyads=None)`, `build_units(..., spares=None, preset=...)`, `main_units(study, set, preset)`, `Unit`, `ABatch`, `BDyadSlot`, `Permutation` | Study A batch table and Study B design table with permutations, swap flags and atom orders | #29 |
| `presets` | `get_preset(name)`, `DEFAULT_PRESET`, `PRESET_NAMES`, `SampleSizePreset.counts()`, `parse_presets(data)` | sample-size presets: confirmatory counts and derived totals | #266 |
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
| `assign` | `build_a_allocation(master, set, *, n_batches=None)`, `build_b_allocation(master, set, *, spares=8, n_dyads=None)`, `check_a_allocation`, `check_b_allocation`, `allocation_seed(master)`, `a_slot_ids`, `b_slot_ids`, `bank_id` | Study A waves, learner slots and book key; Study B roles, banks and menu orders; balance checks | #31 |
| `waves` | `balanced_waves(master, set, batches)` | Study A recruitment waves of confirmatory sets other than 18 batches | #266 |
| `assign_output` | `assign_files(master, study, set)`, `load_list(path)`, `demo_allocation_files()` | list documents, balance report, manifest | #31 |
| `reveal` | `RevealLog(list, log)`: `log_eligibility`, `reveal_next`, `log_bank_unavailable`, `RevealError` | reveal-next stub for the console | #31 |
| `masking` | `find_method_strings(text)`, `assert_masked(text)` | method-string scan for learner-facing files | #31 |
| `run_sheets` | `run_sheet_csv(doc, hash_cell)`, `run_sheet_findings(data, doc, hash_cell)`, `read_run_sheet`, `PackageHashes`, `load_package_hashes`, `parse_package_hashes`, `placeholder_package_hashes`, `hash_cells`, `package_keys` | run-sheet CSV per person and visit; package-hash mapping | #32 |
| `run_sheet_output` | `generate_run_sheets(master, study, set, *, package_hashes=None)`, `render_run_sheets(run)`, `run_sheet_example_files()`, `RunSheetCheckError` | checked run-sheet files and manifest | #32 |
| `checks` | `run_all(master, study, set)`, `build_set`, `check_set`, `SetRun`, `design_check_values`, `assessment_values`, `report`, `RULES` | validation suite over every generated schedule, run sheet and list | #32 |
| `findings` | `Finding(unit, person, visit, rule, detail)`, `format_findings` | check results (also from `orders.visit_schedule_findings`) | #32 |
| `planning` | `design_checks_oracle()`, `RUN_SHEET_COLUMNS`, `TEMPLATE_SHA256`, `FULL_MESSAGE_SLOT_S`, `ATOMIC_SLOT_S`, `ALLOCATION_CHECKS`, `B_SCREENING_MINUTES`, `check_planning(dir, templates=...)` | every design-checks.json field, template header, slot and booking oracles | #32 |

Schemas: [`schema/permutation.schema.json`](schema/permutation.schema.json),
[`schema/curriculum-unit.schema.json`](schema/curriculum-unit.schema.json),
[`schema/visit-schedule.schema.json`](schema/visit-schedule.schema.json) (hidden-answer
material), [`schema/speech-list.schema.json`](schema/speech-list.schema.json);
allocation: `schema/a-slots`, `a-book-key`, `b-dyads`, `assign-manifest`, `reveal-log`
(`.schema.json`); run sheets: `schema/run-sheet-row`, `run-sheets-manifest`,
`package-hashes` (`.schema.json`); presets: `schema/sample-size-presets.schema.json`.

## Development

```sh
uv run --project schedules ruff check --config schedules/pyproject.toml schedules tests/schedules
uv run --project schedules ruff format --config schedules/pyproject.toml --check schedules tests/schedules
(cd schedules && uv run mypy)
uv run --project schedules pytest --import-mode=importlib -p no:cacheprovider tests/schedules
```

Set `AV_PLANNING_DIR` to the external planning-materials folder to also run the tests
that compare it with the source constants (the run-sheet template is read from
`AV_TEMPLATES_DIR`, default `../templates` next to it); they are skipped otherwise.
