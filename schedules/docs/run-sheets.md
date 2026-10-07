# Run sheets and schedule validation (O4.4.4)

This document describes the per-visit run sheets and the validation suite that checks
every generated schedule before a session uses it. File formats and the console contract
are in [`docs/interfaces/schedules.md`](../../docs/interfaces/schedules.md#run-sheets-and-checks-32).
Protocol references: Common procedures §7, Study A protocol §4-§6, Study B protocol §5-§7
and §11, Protocol constants. The oracles are the planning `design-checks.json`,
`assessment-schedule.csv` and the header of the methodology `visit-run-sheet-template.csv`;
they are encoded in `av_schedules.planning` and never copied into this repository.

## 1. Run sheets

One CSV per person slot and visit, one row per block of that person's visit schedule
(#30), in block order. The header is exactly the template header:

`participant_id, visit, block, expected_count, actual_count, start_time, end_time,
comfort_check, phone_locked, hash_check, deviations, operator_signoff`

| Column | Filled by | Value |
| --- | --- | --- |
| `participant_id` | generator | person slot ID, e.g. `A-C07-L03`, `B-C12-M1` |
| `visit` | generator | `D0`, `D7`, `V1`, `V2`, `V3`, `W1`, `W4` |
| `block` | generator | block name of the schedule (`profile_menu`, `atom_menus`, `atomic_lessons`, `message_lessons`, `pre_old`, `trained`, `novel`, `atomic`, `validity`) |
| `expected_count` | generator | items in the block (trials, lessons or menus) |
| `hash_check` | generator, when a package-hash mapping is given | `sha256:<package hash>`; DEMO placeholders `DEMO-placeholder:<hex>`; empty otherwise |
| `actual_count`, `start_time`, `end_time`, `comfort_check`, `phone_locked`, `deviations`, `operator_signoff` | operator or console (#73) | empty |

Rows per visit:

| Visit | Rows (block: expected count) |
| --- | --- |
| A D0 | atomic_lessons 16, message_lessons 36, trained 36, novel 4, atomic 16 |
| A D7 | trained 36, novel 4, atomic 16, validity 16 |
| B V1 | profile_menu 1, atom_menus 8, atomic_lessons 8, message_lessons 8, trained 4, novel 2, atomic 8 |
| B V2 | pre_old 4, atom_menus 4, atomic_lessons 4, message_lessons 12, trained 10, novel 2, atomic 12 |
| B V3 | pre_old 10, atom_menus 4, atomic_lessons 4, message_lessons 16, trained 18, novel 2, atomic 16 |
| B W1 | trained 36, novel 4, atomic 16 |
| B W4 | trained 36, novel 4, atomic 16, validity 16 |

**Person slots, not people.** Run sheets are generated before anyone is allocated, so
`participant_id` holds the slot. The reveal API (#31) binds a slot to a coded
participant ID when it reveals the entry; the console shows that ID (or writes it into
the exported sheet) and keeps the slot ID in its own record. No other personal data goes
into a run sheet.

**Masking.** A run sheet holds no method label, designer, role, scaffold arm, swap flag
or intended answer. Study A sheets are scanned with `find_method_strings` (rule
`masking`). The expected package hash identifies the book's package but not its method;
learners of one book share it, as they share the anonymous book ID in the learner-facing
slot list.

**Package hashes.** The input is a `package-hashes` JSON mapping (format in the interface
document) from the package builder (#13): Study A book ID (`BK-C-7QX4MN`, looked up in
the learner-facing slot list of #31, never the restricted key) or Study B dyad slot ID
(both members share the dyad package) to the package SHA-256. The mapping must be for
the same study and set, match the seed's DEMO status, cover every main unit and name
nothing outside the set; spare dyad slots without a package get an empty cell. The
same expected hash is written on every row of the visit, so the console can verify the
loaded package before each block (Common procedures §7: verify hashes before the visit;
Study B §5.2 and §11: old/new hash checks around the menus). Committed examples use a
DEMO placeholder mapping (`"placeholder": true`); placeholders are written as
`DEMO-placeholder:<hex>` so they can never match a real package.

**Restricted.** Run sheets follow the schedules they are built from: real sets are
written to restricted storage (`schedules/out/`, git-ignored; the CLI refuses a private
seed or output inside a non-ignored work-tree path) and only the manifest hash is
published. The committed examples are DEMO pilot sheets in
[`../examples/demo-run-sheets/`](../examples/demo-run-sheets/README.md).

Line endings: generated files use `\n` (repository convention); the template file uses
`\r\n`. The header text and column order are identical.

## 2. Generating run sheets

```sh
# DEMO (public seed; placeholder package hashes)
uv run --project schedules python -m av_schedules run-sheets --demo-seed DEMO-local \
    --set pilot --demo-placeholder-hashes
# Real set: the set's private master seed (the same as its curriculum, schedules and
# allocation lists) and the package-hash mappings from #13; output to restricted storage
uv run --project schedules python -m av_schedules run-sheets \
    --master-seed-file <path outside the repo> --set pilot \
    --package-hashes <A mapping> --package-hashes <B mapping> --out <restricted folder>
```

The command builds the units, every schedule, the allocation lists and the run sheets
of each requested study and set in memory, runs the whole validation suite on them, and
writes nothing if any check fails (exit 1, findings table on stderr). Otherwise it
writes, under `<out>/<study>/`, `<unit>/run-sheets/<person>/<visit>.csv` and
`<set>-run-sheets-manifest.json` (template hash, SHA-256 of the schedules manifest the
sheets came from, package-hash mapping hash, bookings and the SHA-256 of every sheet).
Like `schedules` and `allocate`, it refuses a seed other than the set's curriculum seed
and overwriting run sheets from another seed without `--force`.

## 3. Validation suite

`av_schedules.checks.run_all(master, study, set_name)` returns a list of `Finding`
(`unit`, `person`, `visit`, `rule`, `detail`; `str()` gives
`"<unit> <person> <visit>: <rule>: <detail>"`, `-` when not applicable; set-level
findings use the set prefix such as `A-C` as the unit). An empty list means the set
passes. `python -m av_schedules check` prints a Markdown report: the sets checked, the
design-check and assessment tables reproduced from the generated schedules, and the
findings table; it exits 1 when there is a finding. CI runs it for DEMO pilot and
confirmatory sets and appends the report to the job summary.

Rules, per visit schedule (the #30 check, `orders.visit_schedule_findings`):

| Rule | Checks |
| --- | --- |
| `blocks` | block sequence, item counts and passes equal the visit plan |
| `order` | protected battery trained -> novel -> atomic, consecutive; pre-old before any menu or lesson; validity after the battery |
| `once-per-pass` | every two-pass block has each item once per pass, pass 1 before pass 2 |
| `content` | each one-pass block holds exactly the expected items |
| `heldout` | no held-out message in lessons, menus, pre-old blocks or `dictionary_messages` |
| `slots` | item slot equals the generator's slot table and the block slot |
| `seconds` | the schedule's stored `assessment` counts and seconds equal the plan |
| `validity` | 8 no-cue and 8 speech trials; speech covers every action and target once; stored no-cue targets |
| `trial-ids` | trial IDs unique and prefixed by person and visit |

Rules added by #32 (oracles from the planning materials and protocol constants, not
from the generator's plan):

| Rule | Checks |
| --- | --- |
| `assessment` | per visit: pre-old, trained, novel, atomic and validity counts and assessment seconds equal the `assessment-schedule.csv` row; every test item uses a 14 s (full message, no-cue, speech) or 9 s (atomic) slot; the person has exactly the study's visits |
| `plays` | scheduled plays per item: lessons 3, menus 8 (profile and atom), test trials 1, no-cue 0 |
| `heldout-exposure` | over all visits of a person: each held-out message due for that person (after the W1/W4 swap) is played exactly once, in the novel block of its visit; Study A's unused H-V1..H-V3 never; no held-out message in any other block, menu or dictionary list; no complete message in a menu |
| `component-availability` | a novel message's two atoms, every message lesson's atoms and every atomic probe are taught by that visit; trained probes only of messages already taught; pre-old probes only of messages taught at earlier visits |
| `training-coverage` | at every teaching visit, the trained messages taught so far use every introduced action and referent index of each family |
| `matrix-partition` | at the end, the taught atoms give 32 legal messages, split into the 18 trained (taught) and 14 held-out messages of the matrix; Study B tests all 14 held-out messages |
| `train-novel-overlap` | no taught message in a novel block and no novel test of a taught message |
| `design-checks` | per person: every design-checks.json field one person's schedules determine (families, atoms, legal, trained, held-out, B novel exposures, growth by wave, primary trials, teaching plays, menu plays, booked minutes); per set: 216 A learners and 128 B participants (pilot 18 and 16), equal to the allocation lists |
| `booking` | each visit's fixed slots fit its booked minutes |
| `allocation` | the #31 count and balance checks (`check_a_allocation`, `check_b_allocation`) |
| `allocation-coverage` | allocation slots equal the scheduled persons, each under its unit; the list's swap flag equals the schedules' |
| `run-sheet` | header equal to the template; one row per block in plan order; pre-filled values match the schedule; `hash_check` equals the expected cell; operator columns empty; a run sheet exists for every schedule |
| `masking` | no Study A method string in any run sheet |

**Reproducing design-checks.json.** `design_check_values(runs)` measures every numeric
field of `design-checks.json` (32 fields, flattened as `growth_counts[0].atoms_total`
etc.) on every generated person and returns the distinct values seen. A field is
reproduced exactly when that list is `[oracle]`. Families, atoms and legal messages
come from the atomic lessons (legal = taught actions x taught referents per family);
trained messages from the message lessons; held-out = legal - trained; B novel
exposures from the novel blocks; growth from V1, V2 and V3 cumulatively; primary trials
from the trained block at A D0/D7 and B W1/W4; teaching and menu plays from the items'
`plays`; assigned learners and participants from the main-slot persons with schedules.
`B_total_main_booked_minutes_per_person` is the 20-minute screening visit plus the
booked minutes of the visits the person has schedules for; the bookings are protocol
inputs (assessment-schedule.csv `booked_minutes`), and the generated part checked is
that each person has those five visits and that their fixed slots fit (`booking`).

**Fault injection.** `tests/schedules/test_design_checks.py` injects faults into
generated schedules (one extra trained trial, a held-out message in a lesson, swapped
blocks, pre-old after teaching, a wrong slot length, a missing atomic probe, a
duplicated trained item within a pass, a held-out message at the wrong visit, a trained
message in a novel block, an unused Study A held-out message tested, an atomic probe
before its lesson, a held-out message in the dictionary list, training that misses an
introduced index, wrong menu plays, slots beyond the booking, a swap flag that differs
from the list, a missing visit), into run sheets (header, counts, operator columns,
hash, a method code, a missing sheet) and into allocation lists (count, role and slot
errors); each is caught with a finding naming the unit, person, visit and rule.

**Cost.** Building and checking every pilot and DEMO confirmatory set of both studies
(1,268 schedules and run sheets) takes about 0.7 s on a laptop; the whole schedules test
suite runs in under a minute.

## 4. For downstream tasks

- #73 operator console: read the run sheet of the revealed slot and visit; show
  `expected_count` against `actual_count`; compare the loaded package hash with
  `hash_check` (strip the `sha256:` prefix); export with the same header.
- #33 reconciliation: expected counts per block are the run-sheet rows (or
  `visit_plan`); `assessment_values` and `visit_assessment` give the oracle counts.
- #34 synthetic data and #78 integrity suite: `build_set` gives complete synthetic sets
  (schedules, allocation, run sheets) for any DEMO seed; `run_all` is the regression
  check, and the findings name the rule that broke.
