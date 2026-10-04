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
| B | dyad slot | both dyad members | `B-P01`..`B-P08` | `B-C01`..`B-C64`, spares `B-S01`..`B-S08` (at most 96) |

Block IDs are `<study>-<P|C>-blkNN` (4 units per block; spare blocks continue the
confirmatory numbering). IDs are sequential and do not encode condition labels;
`sequence` is the stored ID order, not a generation or recruitment order. Pilot and
confirmatory units never share IDs, block IDs or seeds.

## Files: package-safe versus restricted

`python -m av_schedules curriculum (--master-seed-file PATH --set pilot|confirmatory | --demo-seed DEMO-...)`
writes under `schedules/out/<study>/` (git-ignored). For real (non-DEMO) seeds only
`permutation.json` may leave restricted storage before allocation:

| Path | Content | Status |
| --- | --- | --- |
| `<unit>/permutation.json` | labels at indices, atom/wave orders, abstract cells | **package-safe**: nothing derived from the allocation cell, no derived seeds |
| `<unit>/curriculum.csv` | 32 rows with swap flag, realised novel visits, block ID, unit seed | restricted |
| `<set>-batch-table.csv` | Study A batch table | restricted |
| `<set>-design-table.csv` | Study B design table (SQ arm, swap) | restricted |
| `<set>-balance.csv` | balance report | restricted |
| `<set>-manifest.json` | generator version, seed label, SHA-256 of every other file of the set | restricted; publish its SHA-256 |

Allocation-dependent facts (Study B SQ arm and scaffold families, the H-W1/H-W4 swap and
therefore the novel-test schedule, Study A profile and designer) reach a package only when
it is sealed after allocation (#13 adds them from #30/#31 outputs).

All files are UTF-8 with `\n` line endings; JSON uses `indent=2`, sorted keys and a
trailing newline; every CSV ends with a `seed_label` column. The same seed gives
byte-identical files on every platform. Synthetic examples:
[`schedules/examples/demo/`](../../schedules/examples/demo/README.md).

## `permutation.json` (package-safe)

Schema: [`schedules/schema/permutation.schema.json`](../../schedules/schema/permutation.schema.json)
(`format` = `av-schedules/permutation`, `format_version` = 2).

| Field | Meaning |
| --- | --- |
| `demo` | `true` for DEMO-seeded units. Package builders must refuse `demo: true` for participant packages. |
| `seed_label` | `DEMO-...` or `sha256:<SHA-256 of the private master seed>` |
| `study`, `set`, `unit_id`, `unit_kind` | `unit_kind`: `batch` (A), `dyad` or `spare` (B) |
| `shared_by` | `batch` or `dyad`: the file is identical for every book or member of the unit |
| `family_first` | `K` or `Q`: the family that leads the atom order |
| `labels` | `labels[family][role]` = 4 labels at indices 1..4 |
| `atoms` | 16 entries: `atom_id`, `family`, `role`, `index`, `semantic_label`, `matrix_wave`, `order_position` |
| `atom_order` | 16 atom IDs in introduction order (see below) |
| `wave_atom_order` | Study B only: `"1"`: 8 atoms, `"2"`: 4, `"3"`: 4 (menu order per wave) |
| `messages` | 32 entries: `message_id`, `family`, `action_atom`, `referent_atom`, `semantic_action`, `semantic_referent`, `status` (`trained`/`heldout`), `training_wave`, `heldout_set` (abstract set, the same for every unit) |

`atom_order` is, for Study A, the generation and commit order of the atoms (#20) and the
atomic-lesson order (#30), shared by the three books; for Study B, the wave-1, wave-2 and
wave-3 menu orders concatenated (`wave_atom_order`), which is also the bank traversal
order (#26). The file holds no swap flag, novel schedule, SQ arm, scaffold families,
block/grid/cycle position, profile, designer, method labels, roles or seeds.

Format version 1 (unreleased) also carried those allocation fields; version 2 removed them.

## `curriculum.csv` (restricted)

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
| `seed` | 64 hex | unit identifier `sha256(master\|study\|unit_id\|unit)`; never seeds a draw |
| `novel_visit` | `D0`, `D7`, `unused`, `V1`..`W4` or empty | realised test visit after the swap |
| `seed_label` | `DEMO-...` or `sha256:...` | DEMO marker / master-seed fingerprint |

The first nine columns repeat planning `curriculum.csv` and are identical for every unit
except `counterbalance`.

## Design tables (restricted)

`<set>-batch-table.csv` (Study A): `unit_id, set, sequence, block_id, block_position,
profile, designer, swap_w1_w4, family_first, K_action, K_referent, Q_action, Q_referent,
atom_order, seed, seed_label`. `designer` is an anonymous A1 designer ID `D1`..`D3`.

`<set>-design-table.csv` (Study B): `unit_id, set, kind, sequence, block_id,
block_position, sq_arm, structured_family, swap_w1_w4, family_first, K_action,
K_referent, Q_action, Q_referent, V1_atom_order, V2_atom_order, V3_atom_order, seed,
seed_label`.

Label columns list the labels at indices 1..4 joined by `|` (e.g.
`FLIP_CARD|ADD_ONE|ALIGN_ARROW|REMOVE_ONE` means `a1` = FLIP_CARD); order columns join
atom IDs by `|`.

## Balance report and manifest (restricted)

`<set>-balance.csv` columns: `study, set, metric, scope, family, role, item, position,
count, expected, deviation, seed_label`. Per scope: `label_index`, `wave<w>_position` (B),
`family_first`, `swap_w1_w4`, `profile`, `designer` (A), `sq_arm` (B). Scope `all` only:
`message_cell` (item `ACTION+REFERENT`, position `aI-rJ`), `message_heldout`,
`message_novel` (position = visit), `atom_position` and `label_position` (A),
`wave<w>_label_position` (B). `expected` is the mean count; `deviation = count - expected`.

`<set>-manifest.json`: `format` = `av-schedules/manifest`, `format_version` = 1,
`generator`, `demo`, `seed_label`, `study`, `set`, `units`, `spare_units`, `files`
(path to SHA-256) and `balance_max_abs_deviation` (metric to scope to value).

## Seeds

- Private master seeds: 64-256 lowercase hex digits (at least 256 bits), one per set
  (pilot and confirmatory never share a master seed; the CLI enforces it).
- `derive_seed(master, *parts)` = `sha256("{master}|{part1}|{part2}|...")`, lowercase hex;
  each part matches `[A-Za-z0-9._:-]+`. #30 can use
  `derive_seed(master, study, unit, person, visit, block)`.
- Concealed draws (#31 learner method permutations, roles; #30 orders) must derive from
  the master seed with their own purpose parts, never from the public per-unit `seed`
  value or any other published derived seed.
- `SeedStream(seed)` is the portable integer stream: draw k is the first 8 bytes,
  big-endian, of `sha256("{seed}#{k}")`; `randbelow(n)` rejects draws at or above
  `floor(2^64 / n) * n`; `permutation()` is Fisher-Yates from the end.

## Python API

```python
from av_schedules import (
    demo_seed, load_master_seed, private_seed, MasterSeed, derive_seed, SeedStream,
    build_a_batch_table, build_b_design_table, build_units, Unit, ABatch, BDyadSlot,
    curriculum_rows, curriculum_csv, permutation_document, permutation_json,
    novel_by_visit, generate, render_set, write_files, balance_rows, check_planning,
)

build_a_batch_table(master: MasterSeed, set_name: "pilot" | "confirmatory") -> tuple[ABatch, ...]
build_b_design_table(master: MasterSeed, set_name, *, spares: int = 8) -> tuple[BDyadSlot, ...]
build_units(master, study: "A" | "B", set_name, *, spares: int = 8) -> tuple[Unit, ...]
curriculum_rows(unit) -> tuple[dict[str, str], ...]   # 32 rows, CURRICULUM_COLUMNS
permutation_document(unit) -> dict                     # package-safe permutation.json content
novel_by_visit(unit) -> dict[str, list[str]]           # swap-dependent (restricted)
generate(master, study, set_name, *, spares=8) -> dict[str, bytes]  # every file of a set
derive_seed(master, *parts: str) -> str
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

- #13 copies `permutation.json` byte-for-byte into each package and must reject
  `demo: true` for participant packages; it adds the swap-dependent novel schedule, SQ
  arm and profile at sealing, after allocation.
- #26 may read `permutation.json` (labels and `atom_order`) before allocation; it never
  needs the restricted files.
- #30 builds lessons from `atom_order` (A) or `wave_orders` (B), trained messages from
  `trained_cells`, and novel blocks from `novel_by_visit`; `family_first` is a balanced
  per-unit factor available for family order.
- #31 imports `build_a_batch_table` (adds recruitment waves and 4/4/4 learner
  permutations) and `build_b_design_table` (adds roles, banks, profile-menu order and
  concealment). A spare slot should replace a main slot with the same `sq_arm` and
  `swap_w1_w4`.

## Visit schedules (#30)

Producer: lesson and trial order generator (#30). Consumers: session engine (#67),
package builder `schedules/` slot (#13), run sheets (#32), reconciliation (#33),
speech-command recording (#71, speech list only). Design: [`schedules/docs/orders.md`](../../schedules/docs/orders.md).

**Schedule files are hidden-answer material and restricted.** Every item carries the
private intended tuple, and the files carry allocation-dependent facts (the swap-dependent
novel block and `swap_w1_w4`, the Study B scaffold `presentation`). They belong to the
trusted task queue, must never reach a participant- or operator-visible display, and join
a package only when it is sealed after allocation (#13). Pilot and confirmatory schedules
live in restricted storage next to the curriculum files; only hashes are published. The
speech list holds no answers or allocation facts and can be handed to #71.

### Files

`python -m av_schedules schedules (--master-seed-file PATH --set pilot|confirmatory | --demo-seed DEMO-...)`
(options as for `curriculum`) writes under `schedules/out/<study>/`. It uses the same
master seed as the set's curriculum (it refuses when the curriculum in the output folder
comes from another seed) and, like `curriculum`, refuses one private seed for both sets.

| Path | Content |
| --- | --- |
| `<unit>/schedules/<person_id>/<visit>.json` | one visit schedule (schema below) |
| `<set>-speech-list.json` | frozen speech list of the study and set ([`speech-list.schema.json`](../../schedules/schema/speech-list.schema.json)) |
| `<set>-schedule-summary.csv` | `unit_id, unit_kind, person_id, visit, block_position, block, phase, expected_count, passes, slot_s, seconds, shared_by, order_source, seed_label` |
| `<set>-schedules-manifest.json` | `format` = `av-schedules/schedules-manifest`, `demo`, `seed_label`, `units`, `spare_units`, `persons`, `visits` (assessment counts per visit), `files` (path to SHA-256) |

Person slots: Study A `<unit>-L01`..`-L12` (pilot `-L01`..`-L06`), Study B `<unit>-M1`
and `-M2` (spare slots too). The allocation lists (#31) assign methods (A) and roles (B)
to these slots; schedules do not depend on them. Visits: A `D0`, `D7`; B `V1`, `V2`,
`V3`, `W1`, `W4`.

### Visit schedule JSON

Schema: [`schedules/schema/visit-schedule.schema.json`](../../schedules/schema/visit-schedule.schema.json)
(`format` = `av-schedules/visit-schedule`, `format_version` = 1). Top level:

| Field | Meaning |
| --- | --- |
| `hidden_answer` | always `true` |
| `demo`, `seed_label` | as in `permutation.json`; refuse `demo: true` for participants |
| `study`, `set`, `unit_id`, `unit_kind`, `person_id`, `person_slot`, `visit`, `wave` | identity; `wave` = inventory wave at the visit (A: 3) |
| `swap_w1_w4`, `family_first` | unit design factors used for the novel set and lesson family order |
| `permutation_json_sha256` | SHA-256 of the unit's `permutation.json` |
| `assessment` | the visit's `assessment-schedule.csv` row: `pre_old_trained`, `post_trained`, `novel_once`, `atomic`, `assessment_seconds`, `extra_after_protected` |
| `dictionary_messages` | complete messages allowed in teaching or dictionary views up to this visit (cumulative trained set); never a held-out message |
| `blocks` | ordered blocks; run in array order |

Block fields: `block` (`profile_menu`, `atom_menus`, `atomic_lessons`,
`message_lessons`, `pre_old`, `trained`, `novel`, `atomic`, `validity`), `position`,
`phase` (`selection`, `teaching`, `pre_test`, `protected`, `validity`; no feedback in the
last three), `expected_count`, `passes`, `slot_s`, `seconds`, `shared_by` (`person`,
`batch`, `dyad`), `order_source` (`seeded`, `stored`, `fixed`), `seed`, `seed_tokens`,
`validity` (validity block only: `no_cue_targets` in draw order, `speech_list_seed`,
`speech_list_sha256`), `items`.

Item fields:

| Field | Meaning |
| --- | --- |
| `trial_id` | `<person_id>-<visit>-<code>-<NN>`, code `PM`, `AM`, `AL`, `ML`, `PO`, `TR`, `NV`, `AT`, `VA`; unique across all schedules. The engine gives a retry a new ID with `retry_of`. |
| `position`, `pass` | 1-based position in the block; pass 1 or 2 (pass 1 items all precede pass 2) |
| `trial_type` | `profile_menu`, `atom_menu`, `atomic_lesson`, `message_lesson`, `pre_old`, `trained`, `novel`, `atomic`, `no_cue`, `speech` |
| `slot_s`, `plays` | fixed slot (60, 45, 20, 24, 14, 9 s) and scheduled audio plays (8, 8, 3, 3, 1, 1, 1, 1, 0, 1) |
| `message_id`, `atom_id`, `speech_id` | the cue played (one of them, or none for the profile menu and no-cue trials) |
| `trained_status` | `trained`, `heldout` (novel), `atom`, `nonsemantic` (profile menu), `validity` (no-cue, speech) |
| `presentation` | message lessons: `structured` or `dictionary` (B dictionary family); otherwise null |
| `intended` | PRIVATE tuple: `{kind: "message", family, message_id, action_index, referent_index, semantic_action, semantic_referent}` or `{kind: "atom", family, atom_id, role, index, semantic_label}`; null for the profile menu. For no-cue trials it is the private target. |

Guarantees (checked by `check_visit_schedule` and the tests): block counts and seconds
match `assessment-schedule.csv`; one protected battery per visit, trained -> novel ->
atomic; pre-old blocks precede every menu and lesson; every two-pass block shows each
item once per pass; no held-out message in lessons, menus, pre-old blocks or
`dictionary_messages`; each held-out message is tested at most once per person, at its
visit; validity blocks hold 8 no-cue and 8 speech trials covering every action and
target once. Same seed, same bytes.

Seeds: `seed = sha256("{master}|" + "|".join(seed_tokens))` with `seed_tokens` =
`[study, unit_id, person, visit, block]`; for blocks shared by the batch or dyad the
person token is the unit ID. Stored-order blocks (`atom_menus`, `atomic_lessons`)
have `seed: null` and follow `permutation.json` `atom_order` (A) or `wave_atom_order`
(B). Seeds never use the public per-unit `seed` of #29.

### Speech list JSON

`format` = `av-schedules/speech-list`, `format_version` = 1: `demo`, `seed_label`,
`study`, `set`, `seed`, `seed_tokens` (`[study, "<study>-<P|C>", "speech-list"]`) and 8
`commands` (`position`, `speech_id` = `<family>-<ACTION>-<TARGET>` such as
`K-ADD_ONE-B`, `family`, `semantic_action`, `semantic_referent`): one K pairing of the
four actions with trays A-D and one Q pairing with containers E-H. #71 should name its
recordings by `speech_id` so the validity blocks can reference them.

### Python API (#30)

```python
from av_schedules import (
    person_ids, study_visits, visit_plan, BlockPlan, assessment_counts,
    build_visit_schedule, unit_schedules, visit_schedule_json, check_visit_schedule,
    speech_commands, speech_list_document, SpeechCommand,
    generate_schedules, render_schedules,
)

person_ids(unit) -> tuple[str, ...]                 # person slots of a unit
study_visits(study) -> tuple[str, ...]              # ("D0", "D7") or ("V1", ..., "W4")
visit_plan(study, visit) -> tuple[BlockPlan, ...]   # block, count, passes, slot_s, phase, seconds
assessment_counts(study, visit) -> dict[str, int]   # the assessment-schedule.csv row
build_visit_schedule(master, unit, person_id, visit) -> dict    # one schedule document
unit_schedules(master, unit) -> dict[str, dict]     # "<person_id>/<visit>.json" -> document
check_visit_schedule(doc) -> list[str]              # problems "<unit> <person> <visit>: <rule>: ..."
speech_commands(master, study, set_name) -> tuple[SpeechCommand, ...]
generate_schedules(master, study, set_name, *, spares=8) -> dict[str, bytes]  # all files of a set
```

`av_schedules.orders` also exposes `block_order(master, unit, person_id, visit, block)`
(the ordered items of one block with its seed), `seed_tokens`,
`pass_orders`, `alternating_passes`, `sample_with_replacement`, `SLOT_S`, `PLAYS`;
`av_schedules.planning` holds the oracles `ASSESSMENT_SCHEDULE`, `ASSESSMENT_COLUMNS`
and `SCHEDULE_CHECKS`.

Notes for consumers:

- #67 loads one schedule per person and visit, runs blocks and items in array order on
  `slot_s`, and can fail closed with `check_visit_schedule` (or its own rule set) before
  the visit; it should check `permutation_json_sha256` against the package. The schema
  review with the #67 owner is pending.
- #13 places a person's schedule files in the package `schedules/` slot (A: the learner
  slots allocated to the book; B: both members), then calls `seal()`.
- #32 builds run sheets from `visit_plan` / `<set>-schedule-summary.csv` and the
  allocation lists.

## Allocation lists and reveal API (#31)

Producer: allocation lists (#31). Consumers: operator console (#73, reveal API), run
sheets (#32), analysis pipeline (#34, unmasking key), bank builder and register (#26,
#28, dyad slot to bank ID). Design and balance argument:
[`schedules/docs/allocation.md`](../../schedules/docs/allocation.md). **Pending:** the
file-format agreement with the console owner (#73).

`python -m av_schedules allocate [--pilot-seed-file P | --pilot-demo-seed DEMO-...]
[--confirmatory-seed-file C | --confirmatory-demo-seed DEMO-...]` writes, under
`<out>/<study>/` (restricted storage; pilot and confirmatory need different master
seeds, each the same as that set's curriculum seed):

| Path | Audience | Content |
| --- | --- | --- |
| `A/<set>-slots.json` | learner-facing | learner slots in reveal order: slot, batch, wave, profile, anonymous book ID |
| `A/<set>-book-key.json` | restricted (study coordinator) | book ID -> batch, method `A1`/`A2`/`A3`, A1 designer, slots |
| `B/<set>-dyads.json` | concealed until revealed | dyad slots with SQ arm, swap, roles by member slot, bank ID, profile-menu order |
| `<study>/<set>-assign-balance.csv` | coordinator | balance report (curriculum balance-report columns) |
| `<study>/<set>-assign-manifest.json` | publishable | `allocation_seed`, generator, `list_sha256` per list, SHA-256 per file |

Schemas: [`a-slots`](../../schedules/schema/a-slots.schema.json),
[`a-book-key`](../../schedules/schema/a-book-key.schema.json),
[`b-dyads`](../../schedules/schema/b-dyads.schema.json),
[`assign-manifest`](../../schedules/schema/assign-manifest.schema.json),
[`reveal-log`](../../schedules/schema/reveal-log.schema.json) (one log line). Synthetic
examples: [`schedules/examples/demo-allocation/`](../../schedules/examples/demo-allocation/README.md).

Common list fields: `format` (`av-schedules/a-slots`, `av-schedules/a-book-key`,
`av-schedules/b-dyads`), `format_version` = 1, `audience`, `generator`, `demo`,
`seed_label`, `allocation_seed` (`sha256:` + SHA-256 of the master seed, the apparatus
manifest value; never the seed), `study`, `set`, `design_table_sha256` (SHA-256 of the
`<set>-batch-table.csv` or `<set>-design-table.csv` used) and `list_sha256` (SHA-256 of
the canonical JSON of the document without `list_sha256`: sorted keys, separators `,`
and `:`, UTF-8). `load_list(path)` verifies format and hash.

### IDs

| Item | Format | Notes |
| --- | --- | --- |
| Study A learner slot | `A-C07-L03` | `L01`..`L12`, pilot `L01`..`L06` |
| Study B member slot | `B-C12-M1` | `M1` = partner who finished screening first |
| Book ID | `BK-C-7QX4MN` | set code + 6 characters without `A` or `D`; no method meaning |
| Bank ID | `bank-C001` | placeholder by dyad-slot sequence (no draw, not concealed: #26 may use it before allocation); spares `bank-C065`..; pilot `bank-P001`.. |

Pilot and confirmatory IDs never overlap (set code in every ID). Concealed draws (waves,
method permutations, book IDs, roles, menu orders) derive from the master seed with
`alloc:*` purpose parts, never from a unit's public `seed`.

### Entries

`<set>-slots.json`: `counts` (`batches`, `waves`, `slots`, `slots_per_batch`), `waves`
(`wave`, `units` in recruitment order) and `slots`, each with `slot_id`, `unit_id`,
`slot`, `order` (reveal order), `wave`, `wave_position`, `profile`, `book_id`.

`<set>-book-key.json`: `slots_list_sha256` and `books`, each with `book_id`, `unit_id`,
`method`, `designer` (`D1`..`D3` for A1, else null), `slots`.

`<set>-dyads.json`: `counts` (`dyads`, `spares`) and `dyads` (main slots, then spares),
each with `unit_id`, `kind` (`dyad`/`spare`), `order`, `block_id`, `block_position`,
`sq_arm`, `structured_family`, `swap_w1_w4`, `members` (`slot_id`, `member` 1/2,
`role` `active`/`yoked`), `bank_id`, `profile_menu_order` (3 profiles; the first is the
default).

### Reveal API (for #73)

```python
from av_schedules import RevealLog, RevealError

console = RevealLog(list_path, log_path)   # slots or dyads list; clock= defaults to UTC now
rid = console.log_eligibility(["P-0412"], staff="S03",
                              checks={"consent": True, "compatibility": True, "orientation": True})
entry = console.reveal_next(rid, staff="S03")              # next entry, bound to P-0412
console.log_bank_unavailable("bank-C017", staff="S01")     # Study B, before the first reveal
console.revealed(); console.pending(); console.remaining()
```

- Study A: one participant per record; checks `consent`, `compatibility`,
  `orientation`. The entry is the slot entry plus `participant_id`; it never contains a
  method label or designer ID (scanned before return).
- Study B: two participants per record, first the one who finished screening first;
  checks `consent`, `screening`, `compatibility`, `scheduling`. The entry is the dyad
  entry with `participant_id` in each member, plus `replaces` (the main slot a spare
  replaces because its bank is unavailable, else null).
- Entries are revealed in list order (Study A: waves, batches, slots; Study B: design-table
  `sequence`, i.e. complete permuted blocks), each once; a vacancy is never refilled. Errors
  raise `RevealError` (no eligibility record, record already used, list exhausted, no
  spare left, damaged list or log, restricted key).
- The log is JSON Lines: canonical JSON per line with `format`
  (`av-schedules/reveal-log`), `line`, `event` (`eligibility`, `bank_unavailable`,
  `reveal`), `at`, `staff`, `list_sha256`, `prev_sha256` (SHA-256 of the previous line,
  64 zeros for the first) and the event fields (`eligibility_id`, `participant_ids`,
  `checks`, `bank_id`, `entry`).

Masking: `find_method_strings(text)` (`av_schedules.masking`) is the automated scan for
learner-facing files and console payloads: case-sensitive `A1`-`A3` and `D1`-`D3`
substrings and method words (case-insensitive). Atom IDs such as `K-a1` do not match.

### Python API (for #32 and #34)

```python
build_a_allocation(master, set_name) -> AAllocation   # .waves, .slots (ASlot), .books (ABook), .method_of(slot_id)
build_b_allocation(master, set_name, *, spares=8) -> BAllocation   # .dyads (BDyad: members, bank_id, profile_menu_order, active_member)
check_a_allocation(alloc) -> list[str]; check_b_allocation(alloc) -> list[str]   # count and balance checks, [] = pass
assign_files(master, study, set_name, *, spares=8) -> dict[str, bytes]
allocation_seed(master); load_list(path)
# av_schedules.assign: a_slot_ids(unit_id, set_name), b_slot_ids(unit_id), bank_id(set_name, sequence)
```

Run sheets (#32) take the slot IDs and, for Study A, the book ID from the learner-facing
list (never the key). The analyst (#34) joins the key on `book_id` at unmasking.
