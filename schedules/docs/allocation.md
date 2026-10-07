# Allocation lists for both studies (O4.4.3)

This document describes how `av_schedules` builds the concealed allocation lists and the
reveal-next stub for the operator console. File formats and the console contract are in
[`docs/interfaces/schedules.md`](../../docs/interfaces/schedules.md#allocation-lists-and-reveal-api-31).
The lists build on the unit design tables of [`curriculum.md`](curriculum.md): the Study A
batch table (profile, A1 designer, swap flag per batch) and the Study B design table
(SQ arm, swap flag and permuted block per dyad slot). Nothing in those tables changes.

## 1. Person slots

Lists are made before people are known, so they assign *slots*:

| Study | Slot IDs | Meaning |
| --- | --- | --- |
| A | `<batch>-L01`..`-L12` (pilot `-L06`) | learner slots of a batch |
| B | `<dyad slot>-M1`, `-M2` | member 1 = the partner who finished screening first |

Visit schedules (#30) are built per slot with the same IDs. A slot is bound to a coded
participant ID only when the reveal API reveals it.

## 2. Study A

**Recruitment waves** (Study A protocol section 4). The 18 confirmatory batches form 6
waves of 3, each with one batch per profile. The seeded construction also spreads the
other batch factors over time:

1. Seeded orders of the profiles and the designers give a cyclic 3 x 3 Latin square:
   pattern `r` pairs profile `j` with designer `(r + j) mod 3`. Each wave uses one
   pattern, so the three A1 designers in a wave always differ.
2. Each profile x designer pair has two batches (swapped and unswapped, from the batch
   table). A seeded 3 x 3 split, drawn uniformly from the 36 splits whose rows, columns
   and cyclic diagonals each hold 1 or 2 swapped batches (4 or 5 in total), sends one of
   them to waves 1-3 and the other to waves 4-6.
3. Within each half the three patterns are taken in a seeded order, and the batch order
   inside each wave follows a seeded cyclic Latin square, so each profile takes each
   wave position once per half.

Result for every seed: every wave has one batch per profile, three different A1
designers and 1 or 2 swapped batches; waves 1-3 and 4-6 each contain every profile x
designer pair once; each profile is first, second and third in a wave twice. The pilot
has one wave of its 3 batches in a seeded order.

**Slots and books.** Each batch gets a seeded permutation of 4 A1, 4 A2 and 4 A3 over
`L01`..`L12` (pilot 2/2/2 over `L01`..`L06`), unconstrained as the protocol asks. Each
batch x method book gets an anonymous book ID `BK-<P|C>-XXXXXX`: 6 characters drawn
from `BCFGHJKMNPQRTVWXY456789` (no `A`, no `D`), so an ID can never contain a method
code or designer ID. IDs are unique within a set; the set code keeps pilot and
confirmatory IDs apart. These learner-facing IDs are not the anonymous panel IDs of the
round orchestrator (#20).

**Reveal order.** Waves in order, batches in wave order, slots in number order:
`order` 1..216 (pilot 1..18). A revealed slot is used; vacancies after withdrawal are
not refilled (protocol section 4), so the reveal API never re-issues a slot.

**Split outputs.** `<set>-slots.json` is learner-facing: slot, batch, wave, profile and
book ID, nothing else (the profile is not a method label; like every entry it is shown
only once revealed). `<set>-book-key.json` is the restricted key (book ID -> batch,
method, A1 designer, slots) for the study coordinator and for unmasking (#34). A scan
(`av_schedules.masking.find_method_strings`) refuses to write a learner-facing file
that contains `A1`-`A3`, `D1`-`D3` or a method word.

## 3. Study B

The SQ arm and the H-W1/H-W4 swap come from the design table: every block of 4 dyad
slots holds each SQ x swap cell once, so 64 slots give 32/32 SQ and 16/16 swap within
each arm (spares: 4/4 and 2/2). The allocation adds:

- **Roles by member slot.** Blocks are taken in consecutive pairs (main and spare blocks
  separately). In the first block of a pair, a seeded half of the four cells has member
  1 active; the second block uses the other half. So member 1 is active in exactly half
  of each SQ x swap cell (main 8/8, spares 1/1, pilot 1/1), every block has 2/2, and
  every pair of blocks is balanced within each cell, which keeps balance during rolling
  recruitment. The list is never edited to repair an observed imbalance.
- **Dyad order.** Main dyad slots are revealed in design-table `sequence` order, so the
  permuted blocks of 4 give rolling balance (#29 leaves the dyad order to this list).
  Known limit of permuted blocks: once three dyads of a block are revealed, the fourth
  cell is predictable; roles stay seeded within it.
- **Bank IDs.** Placeholders by dyad-slot sequence: `bank-C001`..`bank-C064` for the main
  slots, `bank-C065`..`bank-C072` for the spares, `bank-P001`..`bank-P008` for the pilot.
  The mapping uses no random draw and carries no allocation information, so the bank
  builder (#26) can use it with the package-safe `permutation.json` before allocation;
  the bank register (#28) binds real banks to these IDs.
- **Profile-menu order** (Study B protocol section 5.1). Dyad slots are taken in groups
  of 6 in sequence order; each group holds the 6 orders of P1, P2, P3 as two seeded
  3 x 3 Latin squares (rotations of a seeded base order and of its reflection), in a
  seeded order. Every aligned group of 3 slots has each profile once at each position;
  72 slots give each order 12 times; 64 main slots give 10 or 11. The first profile is
  the default when no choice is made.
- **Spares.** A main slot whose bank is unavailable is replaced, at its position, by the
  first unused spare with the same SQ arm and swap flag (Proposed in #28 and #29).

`<set>-dyads.json` holds all of this. Roles and arms are not masked from staff, but the
list is concealed: entries are revealed one at a time after both partners' eligibility
is logged.

## 4. Concealment and the reveal-next stub

`av_schedules.reveal.RevealLog(list_path, log_path)` is the only intended way for the
console (#73) to read a list:

1. `log_eligibility(ids, staff=..., checks=...)` records an eligibility decision.
   Study A: one coded participant; `consent`, `compatibility` and `orientation` (8/8
   visual practice) must be true. Study B: both partners, first the one who finished
   screening first; `consent`, `screening`, `compatibility` and `scheduling` must be
   true. A participant can have only one record.
2. `reveal_next(eligibility_id, staff=...)` binds the next entry in list order to that
   record and returns it. Each record and each entry is used once.
3. Study B only: `log_bank_unavailable(bank_id, staff=...)` before the first reveal.

The log is JSON Lines, append-only, one canonical line per event, chained by
`prev_sha256`; reopening replays and verifies it, so an edited, reordered or truncated
log or a log of another list is refused. The stub refuses the restricted key, and it
scans every Study A entry for method strings before returning it. It assumes one console
process per log; real concealment (who can read the list file) is a storage and
custody matter for the study coordinator. The file formats still need the agreement of
the console owner (#73).

## 5. Seeds, hashes and the apparatus manifest

- Every draw uses `SeedStream(derive_seed(master, study, token, purpose))` with purposes
  `alloc:waves` (token `A-C`/`A-P`), `alloc:slots` (batch ID),
  `alloc:book:<method>:<attempt>` (batch ID), `alloc:roles` (first block ID of a pair)
  and `alloc:menu` (token `B-C-menuNN`/`B-P-menuNN`). Every token contains the set code,
  so pilot and confirmatory lists never share a derived seed or an ID.
- Concealed draws (method permutations, book IDs, roles, menu orders) are derived from
  the master seed only. They never use the public per-unit `seed` that packages carry,
  so a package holder cannot recompute an allocation.
- The pilot and confirmatory lists must also use different master seeds: `allocate`
  refuses two equal seeds in one call (DEMO seeds included). Use the same master seed as
  the curriculum and visit schedules of that set; every list records
  `design_table_sha256`, the SHA-256 of the batch or design table it used.
- `allocate` and `schedules` share one guard (`cli._derived_seed_conflict`): in the
  output folder, they refuse outputs of the same set (curriculum, schedules, lists) from
  another seed, and a private master seed that the other set's curriculum, schedules or
  lists already used; `--force` only allows replacing the command's own outputs from
  another seed.
- Every list stores `generator`, `seed_label` (DEMO seed, or `sha256:<fingerprint>`),
  `allocation_seed` = `sha256:<SHA-256 of the master seed>` and its own `list_sha256`
  (SHA-256 of its canonical JSON without that field). `allocation_seed` is the value for
  the apparatus manifest field of the same name; the seed itself is never written.
- `<set>-assign-manifest.json` lists the `list_sha256` of every list and the SHA-256 of
  every file. For restricted sets, publish only these hashes.

## 6. Commands

```sh
# DEMO lists (public seeds; examples only)
uv run --project schedules python -m av_schedules allocate \
    --pilot-demo-seed DEMO-p --confirmatory-demo-seed DEMO-c
# Real lists: private seed files outside the repository, output in restricted storage
uv run --project schedules python -m av_schedules allocate \
    --confirmatory-seed-file <path> --out <restricted folder>
```

Options: `--study A|B|both`, `--spares N` (Study B, multiple of 4, default 8), `--out`
(default `schedules/out`, git-ignored), `--force` (overwrite lists from another seed).
Private seeds or private outputs inside a git work tree are refused, as for
`curriculum` and `schedules`.

The committed DEMO examples in [`examples/demo-allocation/`](../examples/demo-allocation/README.md)
use the curriculum example seed for the confirmatory lists and `DEMO-o4.4.3-pilot` for
the pilot lists; the tests regenerate them byte for byte.
