# Curriculum, permutation and holdout generator (O4.4.1)

This document describes how `av_schedules` builds one curriculum per unit and why the
counterbalancing is balanced. File formats are in
[`docs/interfaces/schedules.md`](../../docs/interfaces/schedules.md).

## 1. One source constant

`av_schedules.matrix.MATRIX` encodes the 4 x 4 matrix of the Protocol constants (rows
`a1`..`a4`, columns `r1`..`r4`; the same for families K and Q). Everything else is
derived from it:

| Derived item | Rule |
| --- | --- |
| Training wave of a cell | `Train Vn` gives wave n |
| Held-out set of a cell | `H-V1`, `H-V2`, `H-V3`, `H-W1`, `H-W4` |
| Introduction wave of an index | first wave of a trained cell in that row or column: a1, a2, r1, r2 = 1; a3, r3 = 2; a4, r4 = 3 |
| `components_available_wave` | max of the introduction waves of the two indices |
| Study B first-novel visit | `H-X` is tested at visit X; with the swap, H-W1 and H-W4 trade visits |
| Study A novel use | H-W1 immediate (D0), H-W4 delayed (D7), other held-out sets unused; the swap trades D0 and D7 |

`av_schedules.planning` holds the numeric oracles of the planning `design-checks.json`
(16 atoms, 32 legal messages, 18 trained, 14 held out, growth 8/12/16 atoms,
8/18/32 legal and 4/10/18 trained by wave) and tests reproduce them from the matrix.

The planning CSVs are not in this repository. `python -m av_schedules check-planning <dir>`
compares an external copy with the constant: every column of `curriculum.csv` except the
free-text `counterbalance` note, the label list of `ontology.csv` (whose `matrix_index`
must not fix an index), and the curriculum fields of `design-checks.json` when present.
It also reports drift when a file's SHA-256 differs from the reviewed version in
`PLANNING_SHA256`. The same check runs in the test suite when `AV_PLANNING_DIR` names the
folder; otherwise those two tests are skipped.

## 2. Units, IDs and sets

| Study | Set | Units | IDs |
| --- | --- | --- | --- |
| A | pilot | 3 batches, one per profile | `A-P01`..`A-P03` |
| A | confirmatory | 18 batches | `A-C01`..`A-C18` |
| B | pilot | 8 dyad slots | `B-P01`..`B-P08` |
| B | confirmatory | 64 dyad slots + 8 spare slots | `B-C01`..`B-C64`, `B-S01`..`B-S08` |

A Study A unit is shared by the three method books of the batch; a Study B unit is shared
by both members of the dyad. The API has no method, book or person argument, so there is
no way to produce different tables inside a unit. The abstract matrix is the same for
every unit; only the label permutation, the swap flag and the atom order vary.

IDs are sequential and do not encode condition labels. Pilot and confirmatory units
never share IDs, block IDs or seeds (every seed derivation includes the unit or block
token, which includes the set code).

## 3. Blocks, cycles and cells

Units are numbered in a stored order and grouped into blocks of 4 consecutive units; 4
consecutive blocks form a cycle of 16. Each block contains each of 4 design cells once,
in a seeded order (a permuted block):

- Study A cells: two unswapped and two swapped cells. The 18 confirmatory batches (blocks
  of 4, 4, 4, 4 and 2) are the full factorial profile (3) x designer `D1`..`D3` (3) x swap
  (2). Profiles are dealt by block in a seeded order: each profile takes one of blocks 1-3
  whole; block 4 is split between two profiles along its diagonal cell pairs (0, 3) and
  (1, 2); block 5 goes to the third profile. Within each profile and swap level the
  designers are dealt in a seeded order. So each profile has 6 batches, 3 swapped, and
  each designer makes 2 books per profile (one swapped, one not). The pilot has one
  batch per profile, distinct designers and both swap levels.
- A partial block (the last 2 confirmatory batches, the 3 pilot batches) takes the first
  cell `c` of its seeded order, then the diagonal cell `3 - c` (the other swap level and
  the other `family_first` value), then further cells in seeded order.
- Study B cells: SQ-1 or SQ-2 crossed with the swap flag. Every block of 4 dyad slots
  holds all four cells, so SQ arms stay 2/2 and swap levels 1/1 per arm in every block
  during rolling recruitment. 64 slots give 32/32 SQ and 16/16 swap within each arm.
  Spare slots continue the sequence in complete blocks (8 spares = 2 per cell).

A unit's grid position in its cycle is (row = block within the cycle, column = cell).

## 4. Semantic-label permutations

Each family has two independent label lines (actions onto `a1`..`a4`, referents onto
`r1`..`r4`). For each line and cycle, a seeded base order of the 4 labels is rotated:
the unit at (row, col) gets rotation `(col_shift[col] + row_shift[row]) mod 4`, with
seeded shifts per line and cycle. This is a Latin square on the grid, so:

- every complete block has each label at each index exactly once;
- every complete cycle has each label at each index exactly once per cell, twice per SQ
  arm or per swap level (Study B), and 4 times in total.

Residual imbalance appears only in incomplete blocks or cycles:

| Set | Units | Label-at-index counts | Mean | Max deviation |
| --- | --- | --- | --- | --- |
| A confirmatory | 18 = 4 blocks + 2 | 4 or 5 | 4.5 | 0.5 |
| A confirmatory, per profile | 6 = 1 block + 2 cells of one block | 1 or 2 | 1.5 | 0.5 |
| A confirmatory, per swap level | 9 | 2 or 3 | 2.25 | 0.75 |
| A pilot | 3 (one partial block) | 0 or 1 | 0.75 | 0.75 |
| B confirmatory, main | 64 = 4 cycles | 16 each (8 per arm, 8 per swap level) | 16 | 0 |
| B confirmatory, main + spares | 72 | 18 each | 18 | 0 |
| B pilot | 8 = 2 blocks | 2 each | 2 | 0 |

The balance report lists every count (Section 7).

## 5. Atom introduction orders

Orders interleave the two families atom by atom (starting with `family_first`) and the two
roles every two atoms. Within a family and role, indices follow a seeded base order per
cycle, rotated per unit.

- **Study A**: one order of all 16 atoms, shared by the three method books. It is the
  generation and commit order of the atoms and the atomic-lesson order. The index
  rotation comes from Latin square A; `family_first` and the leading role come from
  square B (two bits). The two squares are orthogonal, so in each complete cycle of 16
  batches every atom occupies every one of the 16 positions exactly once. Over 18
  batches every atom-position count is 1 or 2, and K and Q each lead 9 batches (3 per
  profile).
- **Study B**: one order per wave, for the atom menus (and atomic lessons) of that visit:
  8 atoms at V1, 4 at V2, 4 at V3. `family_first` (shared by all waves) and the leading
  role at V2 come from square B; the wave-1 leading role and index order come from square
  A; V3 starts with the role that came second at V2. The concatenated 16-atom order is
  also the bank traversal order.

Squares A and B are `A = sym[rho[row] ^ gamma[col]]` and `B = 2*rho[row] ^ gamma[col]` over
GF(4) (XOR addition), with seeded row order `rho`, column order `gamma` and symbols `sym`.
Square B is read as two bits, each constant on the cosets of a subgroup `{0, h}`. Two
seeded constraints, `h1 = 3*(gamma0 ^ gamma3)` and `rho0 ^ rho1 = h1`, make the design
balanced within the column pairs that form SQ arms and swap levels and within half cycles.
For Study B this gives, in every complete cycle, each V1 atom at each V1 menu position
once per SQ arm and once per swap level, each V2/V3 atom at each position twice, and K/Q
leading 4/4 per SQ arm. Over 64 dyads, K and Q lead 16/16 per arm; per arm (and per swap
level) each V1 atom is at each V1 position 4 times and each V2/V3 atom at each position 8
times. The pilot (half a cycle) has each V1 atom at each V1 position once and K/Q leading
2/2 per arm.

## 6. Seeds

- Master seed: a private file (`--master-seed-file`, 32-256 characters of
  `[A-Za-z0-9._-]`, never committed; e.g. `secrets.token_hex(32)`) or a public
  `--demo-seed DEMO-...`. Use separate master seed files for pilot and confirmatory sets
  so that a pilot seed can be disclosed without the confirmatory one.
- Derived seeds: `sha256("{master}|{study}|{unit}|{purpose}")`, lowercase hex. Purposes in
  this generator: `unit` (per-unit root seed, written to `curriculum.csv` and
  `permutation.json`), `batch-table` (Study A deal, token `A-C` or `A-P`), `cells` (per
  block, token = block ID), `squares`, `perm:<family>:<role>` and `order:<family>:<role>`
  (per cycle, token `<study>-<set code>-cycNN`).
- Integers come from `SeedStream`: draw k is the first 8 bytes (big-endian) of
  `sha256("{seed}#{k}")`; `randbelow(n)` rejects draws at or above
  `floor(2^64 / n) * n`; shuffles are Fisher-Yates from the end. This avoids any
  dependency on Python's `random` implementation and can be re-implemented elsewhere.
- Outputs record only the master-seed label: the DEMO seed itself, or
  `sha256:<SHA-256 of the private master seed>`.

## 7. Outputs

`python -m av_schedules curriculum --master-seed-file PATH` (or `--demo-seed DEMO-...`)
writes, under `schedules/out/<study>/` (git-ignored):

- `<unit>/curriculum.csv` and `<unit>/permutation.json` for every unit;
- `<set>-batch-table.csv` (A) or `<set>-design-table.csv` (B);
- `<set>-balance.csv`: metrics `label_index`, `atom_position` (A, all units) or
  `wave1_position`..`wave3_position` (B), `family_first`, `swap_w1_w4`, `profile` and
  `designer` (A) or `sq_arm` (B); scopes `all`, `all+spares` (B), per profile (A), per SQ
  arm (B) and per swap level; each row has the count, the expected mean and the deviation;
- `<set>-manifest.json`: generator version, seed label and SHA-256 of every other file.

The command refuses a private seed file, or private output, inside a git work tree where
it is not ignored, and refuses to overwrite a set written from another seed unless
`--force` is given. Identical seeds give byte-identical files on every platform (LF line
endings, UTF-8, sorted JSON keys).

DEMO examples (seed `DEMO-o4.4.1-example`) are committed under
[`examples/demo/`](../examples/demo/README.md); `python -m av_schedules demo-examples`
regenerates them and the tests fail if any byte differs.
