# Lesson and trial order generator (O4.4.2)

This document describes how `av_schedules` turns each unit's curriculum (#29) into
ordered, seeded visit schedules, one per person and visit. File formats are in
[`docs/interfaces/schedules.md`](../../docs/interfaces/schedules.md#visit-schedules-30).
Protocol references: Study A protocol §4-§6, Study B protocol §5-§8, Common procedures
§5-§7, Protocol constants ("Training and tests"); counts are checked against planning
`assessment-schedule.csv` and `design-checks.json` (oracles in `av_schedules.planning`).

## 1. Persons and visits

| Study | Unit | Person slots | Visits |
| --- | --- | --- | --- |
| A | batch `A-C01`..`A-C18` | `<unit>-L01`..`-L12` (pilot `A-P01`..`A-P03`: `-L01`..`-L06`) | `D0`, `D7` |
| B | dyad slot `B-C01`..`B-C64`, spares `B-S01`.., pilot `B-P01`..`B-P08` | `<unit>-M1`, `<unit>-M2` | `V1`, `V2`, `V3`, `W1`, `W4` |

Every person slot gets its own schedule. A schedule never depends on the Study A method
or the Study B role: the allocation lists (#31) map methods and roles onto these slots.
Spare dyad slots get schedules like main slots.

## 2. Visit plans

`visit_plan(study, visit)` derives the ordered blocks from the matrix (counts per wave);
the tests compare the result with `assessment-schedule.csv`.

| Visit | Blocks (count x slot) | Assessment s | Extra |
| --- | --- | ---: | ---: |
| A D0 | atomic lessons 16 x 20 s; message lessons 36 x 24 s (2 passes of 18); trained 36 x 14 s (2 passes); novel 4 x 14 s; atomic 16 x 9 s | 704 | 0 |
| A D7 | trained 36 (2 passes); novel 4; atomic 16; validity 16 x 14 s | 704 | 16 |
| B V1 | profile menu 1 x 60 s; atom menus 8 x 45 s; atomic lessons 8 x 20 s; message lessons 2 x 4; trained 4; novel 2; atomic 8 | 156 | 0 |
| B V2 | pre-old 4 x 14 s; atom menus 4; atomic lessons 4; message lessons 2 x 6; trained 10; novel 2; atomic 12 | 332 | 0 |
| B V3 | pre-old 10; atom menus 4; atomic lessons 4; message lessons 2 x 8; trained 18; novel 2; atomic 16 | 564 | 0 |
| B W1 | trained 36 (2 passes); novel 4; atomic 16 | 704 | 0 |
| B W4 | trained 36 (2 passes); novel 4; atomic 16; validity 16 x 14 s | 704 | 16 |

Assessment seconds count the pre-old and protected slots (14 s full-message, 9 s
atomic). Every visit has exactly one protected battery, always trained -> novel ->
atomic; pre-old blocks come first, before any menu or lesson; the validity block comes
last at the final visit (A D7, B W4). Teaching-play totals: A 48 atomic and 108
whole-message plays; B 16 atom menus x 8 = 128 menu plays per person plus 8 profile-menu
plays.

Content per block:

- **Atomic lessons / atom menus**: the atoms new at the visit (A D0: all 16).
- **Message lessons**: the messages newly trained at the visit (A D0: all 18), twice.
- **Pre-old** (B V2, V3): every message trained at earlier waves, once each.
- **Trained**: every cumulative trained message, once (B V1-V3) or in two passes (A D0,
  A D7, B W1, B W4).
- **Novel**: the held-out messages due at this visit after the H-W1/H-W4 swap
  (`novel_by_visit(unit)`); each held-out message is played to a person at most once.
- **Atomic**: every atom introduced so far.
- **Validity**: 8 no-cue trials and 8 speech trials (Section 4).

No held-out message ever appears in a lesson, menu, pre-old block or the schedule's
`dictionary_messages` list (the cumulative trained set).

## 3. Orders

| Block | Order | Shared by |
| --- | --- | --- |
| A atomic lessons | stored batch atom order (`unit.atom_order`, #29) | batch |
| B atom menus, atomic lessons | stored wave order (`unit.wave_orders`, `wave_atom_order` in `permutation.json`) | dyad |
| B profile menu | single item | dyad |
| A message lessons | seeded per person | person |
| B message lessons | seeded per dyad | dyad |
| pre-old, trained, novel, atomic, validity | seeded per person | person |

- **Passes.** A two-pass block is two independent shuffles of the block's item set, one
  per pass, so every item appears once before any item repeats. The last item of pass 1
  can equal the first of pass 2; no extra no-repeat rule is imposed.
- **Family order in message lessons.** Within a pass the two families alternate item by
  item. Pass 1 starts with the unit's `family_first` (balanced across units by #29) and
  pass 2 with the other family, so each person sees each family lead once; within each
  family the order is a fresh shuffle per pass. The rule does not depend on the method,
  so the order distribution is identical across methods (and across dyad roles).
- **Dyad sharing.** Both dyad members get identical menu and lesson blocks (same items,
  order and seed) so the yoked member's exposure matches the active member's ledger;
  their test orders are drawn separately.
- **Presentation.** Study A message lessons are `structured`; Study B lessons are
  `structured` for the unit's structured family and `dictionary` for the other.

## 4. Validity block and speech list

At the final visit, the validity block draws 8 no-cue targets independently and
uniformly from all 32 legal commands, with replacement (repeats allowed; the targets
are stored in draw order as `no_cue_targets`). It adds the 8 commands of the frozen
speech list and shuffles the 16 trials once. All slots are 14 s; no-cue trials have no
audio (`plays` 0) and keep their private target in `intended`.

The speech list is frozen per study and set (`<set>-speech-list.json`): the four K
actions, in label order, are paired with a seeded permutation of trays A-D and the four Q
actions with a seeded permutation of containers E-H. So each of the 8 actions and each
of the 8 targets appears exactly once. Speech commands are in semantic-label space;
each validity trial maps its command to the unit's matrix cell for the private tuple.
The list is the recording input for #71 (which records all 32 commands, so any list can
be served); it is not per-person hidden-answer material.

## 5. Seeds

- Seeded blocks use `derive_seed(master, study, unit_id, person, visit, block)` (#29) =
  `sha256("{master}|{study}|{unit_id}|{person}|{visit}|{block}")` (lowercase hex). For
  blocks shared by a unit, the person token is the unit ID. The speech list uses the
  tokens `{study}|{study}-{P|C}|speech-list`.
- Each block stores `seed` and `seed_tokens`; the speech list and every validity block
  store the speech-list seed. With the master seed, every seed can be recomputed.
- Orders use `SeedStream(seed)` (#29): `pass_orders` draws one Fisher-Yates shuffle per
  pass; `alternating_passes` draws the leading family's shuffle, then the other's, per
  pass; the validity block draws 8 `randbelow(32)` targets over the cells in planning
  order, then shuffles the 8 no-cue trials followed by the 8 speech commands. Item pools
  are in planning order (messages) or canonical atom order.
- Seeds come only from the master seed and explicit tokens; the public per-unit `seed`
  of #29 is never used. Pilot and confirmatory sets never share seeds (unit IDs differ).
- The same master seed gives byte-identical files on every platform (canonical JSON:
  indent 2, sorted keys, UTF-8, LF).

## 6. Outputs and hidden answers

`python -m av_schedules schedules (--master-seed-file PATH | --demo-seed DEMO-...)`
writes under `schedules/out/<study>/` (git-ignored):

- `<unit>/schedules/<person>/<visit>.json`: one schedule per person and visit;
- `<set>-speech-list.json`: the frozen speech list;
- `<set>-schedule-summary.csv`: one row per person, visit and block (expected counts,
  slots, seconds), the input for run sheets (#32);
- `<set>-schedules-manifest.json`: SHA-256 of every file above.

Schedule files are hidden-answer material (`hidden_answer: true`): every item carries the
private intended tuple. They also hold allocation-dependent facts (the swap-dependent
novel block, the Study B scaffold presentation), so they join a package only at sealing
after allocation. Confirmatory and pilot schedules go to restricted storage with the
curriculum files; only hashes are published. Use the set's own master seed (the same as
its curriculum; pilot and confirmatory seeds differ); the CLI refuses mismatches. The command refuses a private seed
file, or private output, inside a git work tree where it is not ignored.

DEMO examples (seed `DEMO-o4.4.1-example`, the curriculum example seed) are committed
under [`examples/demo/`](../examples/demo/README.md): every visit of `A-C01-L01` and
`B-C01-M1`, the speech lists of all sets, and the pilot summaries and manifests.

## 7. Checks

`check_visit_schedule(doc)` re-derives the expected structure from the matrix and the
visit plan and returns problems named by unit, person, visit and rule: `blocks`,
`order`, `once-per-pass`, `content`, `heldout`, `slots`, `seconds`, `validity`,
`trial-ids`. The tests run it on every schedule of the pilot and confirmatory DEMO sets
and inject faults (extra trained trial, held-out message in a lesson, swapped block
order, repeat within a pass, wrong novel item, broken speech coverage). The
once-per-pass property is tested over 10,000 master seeds (every two-pass block of a
learner and a dyad member per seed).
