# Generation audit: batch DEMO-DRY-P01

Run `DEMO-dry-run-rt-01` (synthetic); **incomplete or not closed**. Unmasked report: names methods. Restricted: generation operator only; never give it to session experimenters or blinded analysts.

## Books

| Book | Method | Designer | Slots | Valid | Invalid | Selector | Bank fallback | Book fallback | Failed generation | Nonfallback |
|---|---|---|---|---|---|---|---|---|---|---|
| DEMO-BK-7QX4 | A1 | D1 | 48 | 42 | 6 | 4 | 0 | 0 | 0 | 0 |
| DEMO-BK-H9TC | A3 | - | 48 | 45 | 3 | 4 | 0 | 0 | 0 | 0 |
| DEMO-BK-M2RW | A2 | - | 48 | 46 | 2 | 3 | 1 | 0 | 0 | 0 |

Valid slots per round (all atoms):

| Book | Round 1 | Round 2 | Round 3 | Round 4 |
|---|---|---|---|---|
| DEMO-BK-7QX4 | 12 | 11 | 8 | 11 |
| DEMO-BK-H9TC | 10 | 11 | 12 | 12 |
| DEMO-BK-M2RW | 11 | 12 | 12 | 11 |

## Slot outcomes by code

All rounds:

| Outcome | DEMO-BK-7QX4 | DEMO-BK-H9TC | DEMO-BK-M2RW |
|---|---|---|---|
| valid | 42 | 45 | 46 |
| invalid_json | 2 | 0 | 0 |
| schema_violation | 0 | 0 | 0 |
| out_of_domain | 0 | 0 | 0 |
| render_fail | 0 | 0 | 0 |
| event_too_short | 2 | 3 | 2 |
| clipping | 0 | 0 | 0 |
| duplicate | 0 | 0 | 0 |
| reserved_collision | 0 | 0 | 0 |
| separation_fail | 0 | 0 | 0 |
| incompatible | 0 | 0 | 0 |
| overflow_input | 0 | 0 | 0 |
| overflow_output | 0 | 0 | 0 |
| timeout | 2 | 0 | 0 |
| llm_server_error | 0 | 0 | 0 |

Per round, book DEMO-BK-7QX4 (A1):

| Outcome | Round 1 | Round 2 | Round 3 | Round 4 |
|---|---|---|---|---|
| valid | 12 | 11 | 8 | 11 |
| invalid_json | 0 | 1 | 1 | 0 |
| event_too_short | 0 | 0 | 2 | 0 |
| timeout | 0 | 0 | 1 | 1 |

Per round, book DEMO-BK-H9TC (A3):

| Outcome | Round 1 | Round 2 | Round 3 | Round 4 |
|---|---|---|---|---|
| valid | 10 | 11 | 12 | 12 |
| event_too_short | 2 | 1 | 0 | 0 |

Per round, book DEMO-BK-M2RW (A2):

| Outcome | Round 1 | Round 2 | Round 3 | Round 4 |
|---|---|---|---|---|
| valid | 11 | 12 | 12 | 11 |
| event_too_short | 1 | 0 | 0 | 1 |

## Atoms

Valid slots of 12 and the committed source per book:

| # | Atom | Appointment | DEMO-BK-7QX4 | DEMO-BK-H9TC | DEMO-BK-M2RW |
|---|---|---|---|---|---|
| 1 | Q-a2 | 1 | 10 / selector | 11 / selector | 12 / selector |
| 2 | K-a4 | 1 | 11 / selector | 11 / selector | 12 / selector |
| 3 | Q-r2 | 1 | 10 / selector | 11 / selector | 11 / fallback_bank |
| 4 | K-r4 | 1 | 11 / selector | 12 / selector | 11 / selector |
| 5 | Q-a3 | 2 | 0 / - | 0 / - | 0 / - |
| 6 | K-a1 | 2 | 0 / - | 0 / - | 0 / - |
| 7 | Q-r1 | 2 | 0 / - | 0 / - | 0 / - |
| 8 | K-r1 | 2 | 0 / - | 0 / - | 0 / - |
| 9 | Q-a4 | 3 | 0 / - | 0 / - | 0 / - |
| 10 | K-a2 | 3 | 0 / - | 0 / - | 0 / - |
| 11 | Q-r3 | 3 | 0 / - | 0 / - | 0 / - |
| 12 | K-r2 | 3 | 0 / - | 0 / - | 0 / - |
| 13 | Q-a1 | 4 | 0 / - | 0 / - | 0 / - |
| 14 | K-a3 | 4 | 0 / - | 0 / - | 0 / - |
| 15 | Q-r4 | 4 | 0 / - | 0 / - | 0 / - |
| 16 | K-r3 | 4 | 0 / - | 0 / - | 0 / - |

## Wall time

Batch generation wall time (sum of the 16 atoms): - min; startup (all components): 0.1 min; operator actions: 0.0 min.

| # | Atom | Atom (s) | Round 1 (s) | Round 2 (s) | Round 3 (s) | Round 4 (s) |
|---|---|---|---|---|---|---|
| 1 | Q-a2 | 1008.9 | 256.8 | 267.2 | 222.5 | 262.3 |
| 2 | K-a4 | 983.3 | 233.8 | 252.4 | 258.5 | 238.4 |
| 3 | Q-r2 | 989.2 | 226.8 | 232.9 | 268.3 | 261.2 |
| 4 | K-r4 | 993.0 | 258.9 | 231.5 | 265.2 | 237.4 |
| 5 | Q-a3 | - | - | - | - | - |
| 6 | K-a1 | - | - | - | - | - |
| 7 | Q-r1 | - | - | - | - | - |
| 8 | K-r1 | - | - | - | - | - |
| 9 | Q-a4 | - | - | - | - | - |
| 10 | K-a2 | - | - | - | - | - |
| 11 | Q-r3 | - | - | - | - | - |
| 12 | K-r2 | - | - | - | - | - |
| 13 | Q-a1 | - | - | - | - | - |
| 14 | K-a3 | - | - | - | - | - |
| 15 | Q-r4 | - | - | - | - | - |
| 16 | K-r3 | - | - | - | - | - |

| Appointment | Wall (min) |
|---|---|
| 1 | 66.2 |
| 2 | - |
| 3 | - |
| 4 | - |

## Effort

| Book | Method | Startup (min) | Operator (min) | Rater time (min) | Design active (min) | Familiarization (min) | Model runtime (min) | Tokens in | Tokens out |
|---|---|---|---|---|---|---|---|---|---|
| DEMO-BK-7QX4 | A1 | 0.0 | 0.0 | 48.0 | 18.0 | 0.0 | - | - | - |
| DEMO-BK-H9TC | A3 | 0.1 | 0.0 | 48.0 | - | - | 2.4 | 110801 | 2898 |
| DEMO-BK-M2RW | A2 | 0.0 | 0.0 | 48.0 | - | - | - | - | - |

Machines: not supplied (`--machines`).

## Diversity and durations

| Book | Candidate diversity | Committed diversity | 450 ms | 600 ms | 750 ms | 900 ms | Message min (ms) | Message max (ms) | Outside 1,100-2,000 ms |
|---|---|---|---|---|---|---|---|---|---|
| DEMO-BK-7QX4 | 0.473308 | 0.486803 | 2 | 1 | 1 | 0 | 1250 | 1400 | 0 |
| DEMO-BK-H9TC | 0.480183 | 0.492249 | 0 | 1 | 1 | 2 | 1550 | 2000 | 0 |
| DEMO-BK-M2RW | 0.313925 | 0.409573 | 2 | 1 | 0 | 1 | 1250 | 1550 | 0 |

Diversity: mean pairwise 12-feature distance among the valid candidates of each atom (averaged over atoms with two or more), and among the committed atoms.

## Checks

- Slot refusals: 0; bank scans: 1.
- **57 problem(s):**
  - atom K-a1: no atom_start/atom_end timing
  - atom K-a2: no atom_start/atom_end timing
  - atom K-a3: no atom_start/atom_end timing
  - atom K-r1: no atom_start/atom_end timing
  - atom K-r2: no atom_start/atom_end timing
  - atom K-r3: no atom_start/atom_end timing
  - atom Q-a1: no atom_start/atom_end timing
  - atom Q-a3: no atom_start/atom_end timing
  - atom Q-a4: no atom_start/atom_end timing
  - atom Q-r1: no atom_start/atom_end timing
  - atom Q-r3: no atom_start/atom_end timing
  - atom Q-r4: no atom_start/atom_end timing
  - book DEMO-BK-7QX4: 16 decision records (expected 64)
  - book DEMO-BK-7QX4: 4 atoms committed (expected 16)
  - book DEMO-BK-7QX4: 48 slot records (expected 192)
  - book DEMO-BK-7QX4: atom K-a1 has 0 slot records (expected 12)
  - book DEMO-BK-7QX4: atom K-a2 has 0 slot records (expected 12)
  - book DEMO-BK-7QX4: atom K-a3 has 0 slot records (expected 12)
  - book DEMO-BK-7QX4: atom K-r1 has 0 slot records (expected 12)
  - book DEMO-BK-7QX4: atom K-r2 has 0 slot records (expected 12)
  - book DEMO-BK-7QX4: atom K-r3 has 0 slot records (expected 12)
  - book DEMO-BK-7QX4: atom Q-a1 has 0 slot records (expected 12)
  - book DEMO-BK-7QX4: atom Q-a3 has 0 slot records (expected 12)
  - book DEMO-BK-7QX4: atom Q-a4 has 0 slot records (expected 12)
  - book DEMO-BK-7QX4: atom Q-r1 has 0 slot records (expected 12)
  - book DEMO-BK-7QX4: atom Q-r3 has 0 slot records (expected 12)
  - book DEMO-BK-7QX4: atom Q-r4 has 0 slot records (expected 12)
  - book DEMO-BK-H9TC: 16 decision records (expected 64)
  - book DEMO-BK-H9TC: 4 atoms committed (expected 16)
  - book DEMO-BK-H9TC: 48 slot records (expected 192)
  - book DEMO-BK-H9TC: atom K-a1 has 0 slot records (expected 12)
  - book DEMO-BK-H9TC: atom K-a2 has 0 slot records (expected 12)
  - book DEMO-BK-H9TC: atom K-a3 has 0 slot records (expected 12)
  - book DEMO-BK-H9TC: atom K-r1 has 0 slot records (expected 12)
  - book DEMO-BK-H9TC: atom K-r2 has 0 slot records (expected 12)
  - book DEMO-BK-H9TC: atom K-r3 has 0 slot records (expected 12)
  - book DEMO-BK-H9TC: atom Q-a1 has 0 slot records (expected 12)
  - book DEMO-BK-H9TC: atom Q-a3 has 0 slot records (expected 12)
  - book DEMO-BK-H9TC: atom Q-a4 has 0 slot records (expected 12)
  - book DEMO-BK-H9TC: atom Q-r1 has 0 slot records (expected 12)
  - book DEMO-BK-H9TC: atom Q-r3 has 0 slot records (expected 12)
  - book DEMO-BK-H9TC: atom Q-r4 has 0 slot records (expected 12)
  - book DEMO-BK-M2RW: 16 decision records (expected 64)
  - book DEMO-BK-M2RW: 4 atoms committed (expected 16)
  - book DEMO-BK-M2RW: 48 slot records (expected 192)
  - book DEMO-BK-M2RW: atom K-a1 has 0 slot records (expected 12)
  - book DEMO-BK-M2RW: atom K-a2 has 0 slot records (expected 12)
  - book DEMO-BK-M2RW: atom K-a3 has 0 slot records (expected 12)
  - book DEMO-BK-M2RW: atom K-r1 has 0 slot records (expected 12)
  - book DEMO-BK-M2RW: atom K-r2 has 0 slot records (expected 12)
  - book DEMO-BK-M2RW: atom K-r3 has 0 slot records (expected 12)
  - book DEMO-BK-M2RW: atom Q-a1 has 0 slot records (expected 12)
  - book DEMO-BK-M2RW: atom Q-a3 has 0 slot records (expected 12)
  - book DEMO-BK-M2RW: atom Q-a4 has 0 slot records (expected 12)
  - book DEMO-BK-M2RW: atom Q-r1 has 0 slot records (expected 12)
  - book DEMO-BK-M2RW: atom Q-r3 has 0 slot records (expected 12)
  - book DEMO-BK-M2RW: atom Q-r4 has 0 slot records (expected 12)

## Sources

| File | SHA-256 |
|---|---|
| config.json | f8edf48d4471a369589eae34e8a79fd69fc9a62d4a281705fcf263f26bb5d921 |
| logs/commits.jsonl | 766c60d4c85fc45ce5bee12950fa8eb4d2814de7e6007a8d8a37eeca884631fe |
| logs/decisions.jsonl | d771922164224c7694458667f5f063d46b09fc8a87e0fe80b6d3131e636cda6c |
| logs/fallback-scans.jsonl | 8af709f01d8d4cc8af5ccb3342c621d7803cfe4eba738b0fbe72ff583b5557aa |
| logs/ratings.jsonl | 134ff3e2fc8726428ffd68cfafe65b7a6bdab091b759dacce1ffed40fb80f0c0 |
| logs/slots.jsonl | 6dda5f86ef9ffea3e7f2a95dc4be60fcbc33e201302d5aadf996f3f0b2c3b8d4 |
| logs/timing.jsonl | 34ffc2c66ad9e850c5add8b82b101e24213c6b29dfc573f28926b10edb082cde |
| run-manifest.json | e6cc3b5f4eec7795cb024ffafc2913140098b57f7bc89da5536ca19971cd2ec8 |
