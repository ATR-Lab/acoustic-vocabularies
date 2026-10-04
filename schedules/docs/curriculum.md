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
| B | confirmatory | 64 dyad slots + 8 spare slots (at most 96) | `B-C01`..`B-C64`, `B-S01`..`B-S08` |

A Study A unit is shared by the three method books of the batch; a Study B unit is shared
by both members of the dyad. The API has no method, book or person argument, so there is
no way to produce different tables inside a unit. The abstract matrix is the same for
every unit; only the label permutation, the swap flag and the atom order vary.

IDs are sequential and do not encode condition labels. `sequence` is the stored ID order,
not a generation or recruitment order (recruitment waves and dyad order belong to #31).
Pilot and confirmatory units never share IDs, block IDs or seeds (every seed derivation
includes a unit or design token that contains the set code), and the CLI refuses to use
one private master seed for both sets.

## 3. Blocks, cycles and grid columns

Units are numbered in a stored order and grouped into blocks of 4 consecutive units; 4
consecutive blocks form a cycle of 16. Each block contains each of the 4 grid columns
once, in a seeded order (a permuted block). A unit's grid position is (row = block within
the cycle, column).

- **Study A**: columns 1 and 3 are swapped (H-W1/H-W4), columns 0 and 2 unswapped, so
  every block has 2 swapped batches. The 18 confirmatory batches (blocks of 4, 4, 4, 4
  and 2) are the full factorial profile (3) x designer `D1`..`D3` (3) x swap (2).
  Profiles are dealt by block (seeded): each of blocks 1-3 goes to one profile, block 4
  is split between two profiles along its diagonal column pairs (0, 3) and (1, 2), and
  block 5 goes to the third profile. Within each profile and swap level the designers are
  dealt in a seeded order. So each profile has 6 batches, 3 swapped, and each designer
  makes 2 books per profile (one swapped, one not). The pilot has one batch per profile,
  distinct designers and both swap levels.
- **Study B**: columns are SQ-1 or SQ-2 crossed with the swap flag. Every block of 4 dyad
  slots holds all four, so SQ arms stay 2/2 and swap levels 1/1 per arm in every block
  during rolling recruitment. 64 slots give 32/32 SQ and 16/16 swap within each arm.
  Spare slots continue the sequence in complete blocks (8 spares = 2 per column).
- A partial block (the last 2 confirmatory A batches, the 3 pilot A batches) takes the
  first column `c` of its seeded order, then the diagonal column `3 - c` (the other swap
  level and the other `family_first` value), then further columns in seeded order.

## 4. Semantic-label permutations

Each cycle draws a row order `rho` and a column order `gamma` of GF(4) = {0, 1, 2, 3}
(addition XOR) and uses the three mutually orthogonal Latin squares
`L_k[row][col] = k*rho[row] ^ gamma[col]` (k = 1, 2, 3). Every label line (actions onto
`a1`..`a4`, referents onto `r1`..`r4`, per family) has a seeded base order per cycle; the
unit at (row, col) puts `base[i ^ shift]` at index `i + 1`, with shift `L_1` for actions
and `L_3` for referents. Consequences, in every complete cycle of 16 units:

- each label sits at each index once per block (row) and once per grid column, so 4 times
  per cycle, twice per SQ arm or per swap level, and once per Study B cell;
- because `L_1` and `L_3` are orthogonal, the 16 units carry 16 different pairs of action
  and referent orders, and **each semantic message (e.g. ADD_ONE + tray B) sits at each
  of the 16 matrix cells exactly once**; so each semantic message is held out in exactly
  7 units and trained in 9;
- `gamma1 ^ gamma3 = 3` makes the swapped columns {1, 3} a coset of {0, 3}; the two units
  that place a message in the two H-W1 cells share a row and have columns differing by
  `^ 2`, and the two H-W4 units have columns differing by `^ 1`, so each semantic message
  is the H-W1 novel test in one swapped and one unswapped unit, and the same for H-W4:
  in Study A each message is the D0 novel test exactly twice and the D7 novel test twice
  per cycle; in Study B it is tested at W1 twice and at W4 twice per cycle.

What stays fixed within a cycle (by construction; the bases are redrawn every cycle):
each line takes only 4 of the 24 possible orders, all related by XOR shifts, so the
pairing of labels into the index pairs {1,2}/{3,4}, {1,3}/{2,4} and {1,4}/{2,3} is
constant within a cycle (for example, which two actions share wave 1 in Study B has two
possible values per cycle); and the K and Q shifts are the same squares, so a unit's Q
orders are determined by its K orders within a cycle (with independent bases).

Balance of label at index (counts per family, role, label and index):

| Set | Units | Counts | Mean | Max deviation |
| --- | --- | --- | --- | --- |
| A confirmatory | 18 = 4 blocks + 2 | 4 or 5 | 4.5 | 0.5 |
| A confirmatory, per profile | 6 = 1 block + 2 cells of one block | 1 or 2 | 1.5 | 0.5 |
| A confirmatory, per swap level | 9 | 2 or 3 | 2.25 | 0.75 |
| A pilot | 3 (one partial block) | 0 or 1 | 0.75 | 0.75 |
| B confirmatory, main | 64 = 4 cycles | 16 each (8 per arm, 8 per swap level, 4 per cell) | 16 | 0 |
| B confirmatory, main + spares | 72 | 18 each | 18 | 0 |
| B pilot | 8 = 2 blocks | 2 each overall; within an SQ arm or swap level 0, 1 or 2 | 2 (1) | 0 (1) |

Message level: A confirmatory, each semantic message at each cell once in batches 1-16
(1 or 2 times over 18), held out in 7-9 batches, the D0 (or D7) novel test in 2-3
batches. B confirmatory: each message at each cell 4 times, held out in 28 dyads, tested
at W1 in 8 and at W4 in 8, at V1/V2/V3 in 4 each.

## 5. Atom introduction orders

Orders interleave the two families atom by atom (starting with `family_first`) and the two
roles every two atoms.

- **Study A**: one order of all 16 atoms, shared by the three method books; it is the
  generation and commit order of the atoms and the atomic-lesson order. Each (family,
  role) track lists its indices as `base[k] ^ t` with a seeded base per cycle and `t` the
  other of `L_1`/`L_3` than the line's label shift; `family_first` and the leading role
  are the two coset bits of `L_2`. Since `t` is orthogonal to `L_2`, each matrix atom sits
  at each of the 16 positions exactly once per cycle; since the label shift XOR `t` is
  constant along a row and `L_2` is Latin in rows, **each semantic label also sits at each
  position exactly once per cycle**. Over 18 batches both counts are 1 or 2, and K and Q
  each lead 9 batches (3 per profile). Fixed within a block: each track introduces its
  semantic labels in the same order in the 4 batches of a block (their matrix atoms and
  the interleaving differ); blocks 1-3 belong to one profile each.
- **Study B**: one order per wave, for the atom menus (and atomic lessons) of that visit:
  8 atoms at V1, 4 at V2, 4 at V3. `family_first` (shared by all waves) and the leading
  role at V2 are the coset bits of `L_2`; the wave-1 leading role and index order are the
  two bits of `A = sym[L_1]`; V3 starts with the role that came second at V2. The
  concatenated 16-atom order is also the bank traversal order. Two seeded constraints,
  `h1 = 3*(gamma0 ^ gamma3)` and `rho0 ^ rho1 = h1`, balance the design within the column
  pairs that form SQ arms and swap levels and within half cycles: per complete cycle each
  V1 atom is at each V1 menu position once per SQ arm and once per swap level, each V2/V3
  atom at each position twice, and K/Q lead 4/4 per SQ arm. Over 64 dyads K and Q lead
  16/16 per arm. The pilot (half a cycle) has each V1 atom at each V1 position once and
  K/Q leading 2/2 per arm. Semantic labels are exactly balanced over V2 and V3 positions
  (4 each over 64 dyads); over V1 positions they are only approximately balanced (counts
  0-10, mean 4), because the V1 order bits share square `L_1` with the action labels.

## 6. Seeds

- Master seed: a private file (`--master-seed-file`, 64-256 lowercase hex digits, i.e. at
  least 256 bits, because `sha256(master)` is published as the fingerprint; e.g.
  `secrets.token_hex(32)`; never committed) or a public `--demo-seed DEMO-...`. Each
  private master seed serves one set: `--set both` is refused with a private seed, and so
  is a seed whose label already appears in the other set's manifest in the output folder.
- Derived seeds: `derive_seed(master, *parts) = sha256("{master}|{part1}|...")`, lowercase
  hex. This generator uses `(study, unit, purpose)`: `unit` (the per-unit identifier
  written to `curriculum.csv`), `batch-table` (Study A deal, token `A-C` or `A-P`), `cells`
  (per block, token = block ID), `squares`, `perm:<family>:<role>` and
  `order:<family>:<role>` (per cycle, token `<study>-<set code>-cycNN`).
- The per-unit `seed` value is a public identifier: nothing here is drawn from it, and it
  must never seed a concealed draw. Concealed draws (learner method permutations, roles,
  orders) derive from the master seed with their own purpose tokens, e.g.
  `derive_seed(master, "A", "A-C01", "methods")`.
- Integers come from `SeedStream`: draw k is the first 8 bytes (big-endian) of
  `sha256("{seed}#{k}")`; `randbelow(n)` rejects draws at or above
  `floor(2^64 / n) * n`; shuffles are Fisher-Yates from the end. This avoids any
  dependency on Python's `random` implementation and can be re-implemented elsewhere.
- Outputs record only the master-seed label: the DEMO seed itself, or
  `sha256:<SHA-256 of the private master seed>`.

## 7. Outputs and what is restricted

`python -m av_schedules curriculum --master-seed-file PATH --set pilot|confirmatory` (or
`--demo-seed DEMO-...`) writes, under `schedules/out/<study>/` (git-ignored):

| File | Content | Status for real seeds |
| --- | --- | --- |
| `<unit>/permutation.json` | labels at indices, atom/wave orders, abstract cells | package-safe: no allocation-cell information, no derived seeds |
| `<unit>/curriculum.csv` | 32 rows incl. swap flag, realised novel visits, block ID, unit seed | restricted |
| `<set>-batch-table.csv` / `<set>-design-table.csv` | profile, designer / SQ arm, swap, permutation, orders | restricted |
| `<set>-balance.csv` | balance report (Section 4) | restricted (reveals the design) |
| `<set>-manifest.json` | generator version, seed label, SHA-256 of every file | its SHA-256 may be published |

Every CSV ends with a `seed_label` column (`DEMO-...` marks DEMO output). Balance report
metrics: `label_index`, `wave<w>_position` (B), `family_first`, `swap_w1_w4`, `profile` and
`designer` (A) or `sq_arm` (B) per scope (`all`, `all+spares`, per profile, SQ arm and swap
level); and, for scope `all`, `message_cell`, `message_heldout`, `message_novel`,
`atom_position` and `label_position` (A) or `wave<w>_label_position` (B). Each row has the
count, the expected mean and the deviation.

The command checks every requested set before writing any. It refuses a private seed file
or private output inside a git work tree where it is not ignored; if a `.git` entry is
found but git cannot be run, it refuses too. It refuses to overwrite a set written from
another seed unless `--force` is given, and when it replaces a set it deletes the files
the old manifest listed that the new output no longer contains (no stale unit folders).
Identical seeds give byte-identical files on every platform (LF line endings, UTF-8,
sorted JSON keys).

DEMO examples (seed `DEMO-o4.4.1-example`) are committed under
[`examples/demo/`](../examples/demo/README.md); `python -m av_schedules demo-examples`
regenerates them and the tests fail if any byte differs.
