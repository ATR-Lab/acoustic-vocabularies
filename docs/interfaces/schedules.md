# Schedules interface (`av_schedules`): curriculum and permutation

Producer: curriculum, permutation and holdout generator (#29). Consumers: package
builder (#13), session engine (#67), round orchestrator (#20), Study B bank builder
(#26), lesson and trial order generator (#30), allocation lists (#31), run sheets (#32).

This file defines the curriculum formats. Visit schedules (#30), allocation lists (#31)
and run sheets (#32) add their own sections when they are built. The design and its
balance guarantees are in [`schedules/docs/curriculum.md`](../../schedules/docs/curriculum.md).

The package is used from this repository as an editable or path dependency
(`av-schedules @ {root}/schedules`). It is pure and deterministic: no network, no clock,
no global random state.

## Units and IDs

| Study | Unit | Shared by | Pilot IDs | Confirmatory IDs |
| --- | --- | --- | --- | --- |
| A | matching batch | the three method books of the batch | `A-P01`..`A-P03` | `A-C01`..`A-C18` |
| B | dyad slot | both dyad members | `B-P01`..`B-P08` | `B-C01`..`B-C64`, spares `B-S01`..`B-S08` |

Block IDs are `<study>-<P|C>-blkNN` (4 units per block; spare blocks continue the
confirmatory numbering). IDs are sequential and do not encode condition labels. Pilot and confirmatory units
never share IDs, block IDs or seeds.

## Files

`python -m av_schedules curriculum (--master-seed-file PATH | --demo-seed DEMO-...)`
writes under `schedules/out/<study>/` (git-ignored; real pilot and confirmatory outputs go
to restricted storage, only hashes are published):

| Path | Content |
| --- | --- |
| `<unit>/curriculum.csv` | 32 message rows for the unit |
| `<unit>/permutation.json` | the unit's permutation, atom order and messages (the file each package carries) |
| `<set>-batch-table.csv` | Study A batch table |
| `<set>-design-table.csv` | Study B design table |
| `<set>-balance.csv` | balance report |
| `<set>-manifest.json` | generator version, seed label, SHA-256 of every other file of the set |

All files are UTF-8 with `\n` line endings; JSON uses `indent=2`, sorted keys and a
trailing newline. The same seed gives byte-identical files on every platform. Synthetic
examples: [`schedules/examples/demo/`](../../schedules/examples/demo/README.md).

## `curriculum.csv`

Schema for one row: [`schedules/schema/curriculum-unit.schema.json`](../../schedules/schema/curriculum-unit.schema.json).
Exactly 32 rows in planning order (family K then Q, action index 1..4, referent index
1..4). Columns, in order:

| Column | Values | Meaning |
| --- | --- | --- |
| `family` | `K`, `Q` | |
| `action_index`, `referent_index` | `1`..`4` | matrix indices |
| `message_id` | `K-a1-r2` | abstract message ID |
| `components_available_wave` | `1`..`3` | first wave at which both atoms are introduced |
| `training_wave` | `1`..`3` or empty | abstract training wave (Study A teaches all 18 on D0) |
| `B_first_novel_visit_default` | `V1`..`W4` or empty | Study B test visit before the swap |
| `A_novel_default` | `immediate`, `delayed`, `unused` or empty | Study A use before the swap |
| `counterbalance` | `A-C-blk01` | block ID of the stored counterbalancing assignment |
| `unit_id` | `A-C01`, `B-S03` | |
| `semantic_action`, `semantic_referent` | labels | the unit's permutation applied to the indices |
| `heldout_set` | `H-V1`..`H-W4` or empty | |
| `swap_w1_w4` | `0`, `1` | H-W1/H-W4 swap for this unit |
| `seed` | 64 hex | unit root seed `sha256(master\|study\|unit_id\|unit)` |
| `novel_visit` | `D0`, `D7`, `unused`, `V1`..`W4` or empty | realised test visit after the swap |

The first nine columns repeat planning `curriculum.csv` and are identical for every unit
except `counterbalance`.

## `permutation.json`

Schema: [`schedules/schema/permutation.schema.json`](../../schedules/schema/permutation.schema.json)
(`format` = `av-schedules/permutation`, `format_version` = 1).

| Field | Meaning |
| --- | --- |
| `demo` | `true` for DEMO-seeded units. Package builders must refuse `demo: true` for participant packages. |
| `seed_label` | `DEMO-...` or `sha256:<SHA-256 of the private master seed>` |
| `study`, `set`, `unit_id`, `unit_kind` | `unit_kind`: `batch` (A), `dyad` or `spare` (B) |
| `shared_by` | `batch` or `dyad`: the file is identical for every book or member of the unit |
| `seed` | unit root seed (as in the CSV) |
| `design` | `sequence`, `block_id`, `block_position`, `cycle`, `grid_row`, `grid_col` (audit trail) |
| `swap_w1_w4` | boolean |
| `family_first` | `K` or `Q`: the family that leads the atom order |
| `labels` | `labels[family][role]` = 4 labels at indices 1..4 |
| `atoms` | 16 entries: `atom_id`, `family`, `role`, `index`, `semantic_label`, `matrix_wave`, `order_position` |
| `atom_order` | 16 atom IDs in introduction order (see below) |
| `messages` | 32 entries: `message_id`, `family`, `action_atom`, `referent_atom`, `semantic_action`, `semantic_referent`, `status` (`trained`/`heldout`), `training_wave`, `heldout_set`, `novel_visit` |
| `novel_by_visit` | held-out message IDs per visit after the swap: A `D0`, `D7` (4 each); B `V1`, `V2`, `V3` (2 each), `W1`, `W4` (4 each) |
| `curriculum_csv_sha256` | SHA-256 of the unit's `curriculum.csv` |
| `study_a` | `profile` (`P1`..`P3`) |
| `study_b` | `sq_arm` (`SQ-1`/`SQ-2`), `structured_family`, `dictionary_family`, `wave_atom_order` (`"1"`: 8 atoms, `"2"`: 4, `"3"`: 4) |

`atom_order` is, for Study A, the generation and commit order of the atoms (#20) and the
atomic-lesson order (#30), shared by the three books; for Study B, the wave-1, wave-2 and
wave-3 menu orders concatenated (`study_b.wave_atom_order`), which is also the bank
traversal order (#26). The file holds no method labels, roles or designer IDs.

## Design tables

`<set>-batch-table.csv` (Study A): `unit_id, set, sequence, block_id, block_position,
profile, designer, swap_w1_w4, family_first, K_action, K_referent, Q_action, Q_referent,
atom_order, seed`. `designer` is an anonymous A1 designer ID `D1`..`D3`.

`<set>-design-table.csv` (Study B): `unit_id, set, kind, sequence, block_id,
block_position, sq_arm, structured_family, swap_w1_w4, family_first, K_action,
K_referent, Q_action, Q_referent, V1_atom_order, V2_atom_order, V3_atom_order, seed`.

Label columns list the labels at indices 1..4 joined by `|` (e.g.
`FLIP_CARD|ADD_ONE|ALIGN_ARROW|REMOVE_ONE` means `a1` = FLIP_CARD); order columns join
atom IDs by `|`. The Study B design table assigns SQ arms and swap flags, so confirmatory
versions are restricted material.

## Balance report and manifest

`<set>-balance.csv` columns: `study, set, metric, scope, family, role, item, position,
count, expected, deviation`. Metrics: `label_index`, `atom_position` (A) or
`wave1_position`..`wave3_position` (B), `family_first`, `swap_w1_w4`, `profile`,
`designer` (A), `sq_arm` (B). `expected` is the mean count; `deviation = count -
expected`.

`<set>-manifest.json`: `format` = `av-schedules/manifest`, `format_version` = 1,
`generator`, `demo`, `seed_label`, `study`, `set`, `units`, `spare_units`, `files`
(path to SHA-256) and `balance_max_abs_deviation` (metric to scope to value). Publish the
manifest's SHA-256 for restricted sets.

## Seeds

Derived seeds are `sha256("{master}|{study}|{unit}|{purpose}")` (lowercase hex), where
`unit` is a unit ID or a design token. Downstream generators that need per-unit streams
(#30: `master|study|unit|person|visit|block`) use the same formula with their own unit and
purpose tokens. `SeedStream(seed)` is the portable integer stream: draw k is the first 8
bytes, big-endian, of `sha256("{seed}#{k}")`; `randbelow(n)` rejects draws at or above
`floor(2^64 / n) * n`; `permutation()` is Fisher-Yates from the end.

## Python API

```python
from av_schedules import (
    demo_seed, load_master_seed, MasterSeed, derive_seed, SeedStream,
    build_a_batch_table, build_b_design_table, build_units, Unit, ABatch, BDyadSlot,
    curriculum_rows, curriculum_csv, permutation_document, permutation_json,
    novel_by_visit, generate, render_set, write_files, balance_rows, check_planning,
)

build_a_batch_table(master: MasterSeed, set_name: "pilot" | "confirmatory") -> tuple[ABatch, ...]
build_b_design_table(master: MasterSeed, set_name, *, spares: int = 8) -> tuple[BDyadSlot, ...]
build_units(master, study: "A" | "B", set_name, *, spares: int = 8) -> tuple[Unit, ...]
curriculum_rows(unit) -> tuple[dict[str, str], ...]   # 32 rows, CURRICULUM_COLUMNS
permutation_document(unit) -> dict                     # permutation.json content
novel_by_visit(unit) -> dict[str, list[str]]
generate(master, study, set_name, *, spares=8) -> dict[str, bytes]  # every file of a set
derive_seed(master, study, unit, purpose) -> str
```

`Unit` fields (frozen dataclass): `study`, `set_name`, `unit_id`, `kind`, `sequence`,
`block_id`, `block_position`, `cycle`, `grid_row`, `grid_col`, `seed`, `demo`,
`seed_label`, `swap_w1_w4`, `permutation` (`Permutation.labels(family, role)`,
`.label(family, role, index)`, `.atom_label(atom_id)`, `.index_of(...)`), `family_first`,
`atom_order`, `wave_orders` (B: 3 tuples; A: empty). `ABatch` adds `profile`, `designer`;
`BDyadSlot` adds `sq_arm` and the properties `structured_family`, `dictionary_family`.
The matrix helpers (`MATRIX`, `cells()`, `trained_cells(wave)`, `heldout_cells(set)`,
`wave_atoms(wave)`, `index_waves()`, `novel_visit(study, set, swap)`) are in
`av_schedules.matrix`; oracle counts from planning `design-checks.json` are in
`av_schedules.planning` (`DESIGN_CHECKS`, `GROWTH_COUNTS`).

Notes for consumers:

- #13 copies `permutation.json` byte-for-byte into each package and can verify
  `curriculum_csv_sha256`; it must reject `demo: true` for participant packages.
- #30 builds lessons from `atom_order` (A) or `wave_orders` (B), trained messages from
  `trained_cells`, and novel blocks from `novel_by_visit`; `family_first` is a balanced
  per-unit factor available for family order.
- #31 imports `build_a_batch_table` (adds recruitment waves and 4/4/4 learner
  permutations) and `build_b_design_table` (adds roles, banks, profile-menu order and
  concealment). A spare slot should replace a main slot with the same `sq_arm` and
  `swap_w1_w4`.
