# Generation audit: batch DEMO-DRY-P01

Run `DEMO-dry-run-accel-01` (synthetic); complete. Unmasked report: names methods. Restricted: generation operator only; never give it to session experimenters or blinded analysts.

## Books

| Book | Method | Designer | Slots | Valid | Invalid | Selector | Bank fallback | Book fallback | Failed generation | Nonfallback |
|---|---|---|---|---|---|---|---|---|---|---|
| DEMO-BK-7QX4 | A1 | D1 | 192 | 166 | 26 | 16 | 0 | 0 | 0 | 1 |
| DEMO-BK-H9TC | A3 | - | 192 | 179 | 13 | 16 | 0 | 0 | 0 | 1 |
| DEMO-BK-M2RW | A2 | - | 192 | 182 | 10 | 15 | 1 | 0 | 0 | 0 |

Valid slots per round (all atoms):

| Book | Round 1 | Round 2 | Round 3 | Round 4 |
|---|---|---|---|---|
| DEMO-BK-7QX4 | 39 | 39 | 43 | 45 |
| DEMO-BK-H9TC | 43 | 45 | 45 | 46 |
| DEMO-BK-M2RW | 41 | 46 | 48 | 47 |

## Slot outcomes by code

All rounds:

| Outcome | DEMO-BK-7QX4 | DEMO-BK-H9TC | DEMO-BK-M2RW |
|---|---|---|---|
| valid | 166 | 179 | 182 |
| invalid_json | 8 | 0 | 0 |
| schema_violation | 0 | 0 | 0 |
| out_of_domain | 0 | 0 | 0 |
| render_fail | 0 | 0 | 0 |
| event_too_short | 9 | 13 | 10 |
| clipping | 0 | 0 | 0 |
| duplicate | 0 | 0 | 0 |
| reserved_collision | 0 | 0 | 0 |
| separation_fail | 0 | 0 | 0 |
| incompatible | 0 | 0 | 0 |
| overflow_input | 0 | 0 | 0 |
| overflow_output | 0 | 0 | 0 |
| timeout | 9 | 0 | 0 |
| llm_server_error | 0 | 0 | 0 |

Per round, book DEMO-BK-7QX4 (A1):

| Outcome | Round 1 | Round 2 | Round 3 | Round 4 |
|---|---|---|---|---|
| valid | 39 | 39 | 43 | 45 |
| invalid_json | 2 | 3 | 2 | 1 |
| event_too_short | 4 | 3 | 1 | 1 |
| timeout | 3 | 3 | 2 | 1 |

Per round, book DEMO-BK-H9TC (A3):

| Outcome | Round 1 | Round 2 | Round 3 | Round 4 |
|---|---|---|---|---|
| valid | 43 | 45 | 45 | 46 |
| event_too_short | 5 | 3 | 3 | 2 |

Per round, book DEMO-BK-M2RW (A2):

| Outcome | Round 1 | Round 2 | Round 3 | Round 4 |
|---|---|---|---|---|
| valid | 41 | 46 | 48 | 47 |
| event_too_short | 7 | 2 | 0 | 1 |

## Atoms

Valid slots of 12 and the committed source per book:

| # | Atom | Appointment | DEMO-BK-7QX4 | DEMO-BK-H9TC | DEMO-BK-M2RW |
|---|---|---|---|---|---|
| 1 | Q-a2 | 1 | 11 / selector | 11 / selector | 12 / selector |
| 2 | K-a4 | 1 | 11 / selector | 11 / selector | 12 / selector |
| 3 | Q-r2 | 1 | 10 / selector | 11 / selector | 12 / selector |
| 4 | K-r4 | 1 | 10 / selector | 11 / selector | 11 / selector |
| 5 | Q-a3 | 2 | 12 / selector | 12 / selector | 12 / selector |
| 6 | K-a1 | 2 | 11 / selector | 12 / selector | 12 / selector |
| 7 | Q-r1 | 2 | 11 / selector | 10 / selector | 11 / fallback_bank |
| 8 | K-r1 | 2 | 10 / selector | 12 / selector | 12 / selector |
| 9 | Q-a4 | 3 | 9 / selector | 10 / selector | 11 / selector |
| 10 | K-a2 | 3 | 8 / selector | 12 / selector | 11 / selector |
| 11 | Q-r3 | 3 | 12 / selector | 11 / selector | 10 / selector |
| 12 | K-r2 | 3 | 10 / selector | 11 / selector | 11 / selector |
| 13 | Q-a1 | 4 | 9 / selector | 11 / selector | 11 / selector |
| 14 | K-a3 | 4 | 11 / selector | 12 / selector | 10 / selector |
| 15 | Q-r4 | 4 | 9 / selector | 11 / selector | 12 / selector |
| 16 | K-r3 | 4 | 12 / selector | 11 / selector | 12 / selector |

## Wall time

Batch generation wall time (sum of the 16 atoms): 268.2 min; startup (all components): 0.1 min; operator actions: 0.0 min.

| # | Atom | Atom (s) | Round 1 (s) | Round 2 (s) | Round 3 (s) | Round 4 (s) |
|---|---|---|---|---|---|---|
| 1 | Q-a2 | 969.9 | 232.4 | 255.0 | 247.5 | 234.4 |
| 2 | K-a4 | 1000.3 | 242.7 | 261.8 | 252.3 | 243.1 |
| 3 | Q-r2 | 1006.9 | 271.2 | 244.7 | 241.8 | 248.7 |
| 4 | K-r4 | 1007.6 | 243.5 | 230.7 | 271.4 | 261.4 |
| 5 | Q-a3 | 985.8 | 251.3 | 224.5 | 266.6 | 242.7 |
| 6 | K-a1 | 973.9 | 230.8 | 244.2 | 254.9 | 243.3 |
| 7 | Q-r1 | 969.0 | 238.8 | 227.2 | 250.2 | 251.6 |
| 8 | K-r1 | 1035.6 | 253.3 | 261.1 | 278.7 | 240.2 |
| 9 | Q-a4 | 1032.5 | 213.7 | 265.6 | 271.2 | 274.3 |
| 10 | K-a2 | 1047.7 | 279.3 | 265.7 | 247.1 | 253.7 |
| 11 | Q-r3 | 993.8 | 238.6 | 262.8 | 244.0 | 247.2 |
| 12 | K-r2 | 1020.0 | 260.7 | 262.7 | 234.8 | 260.5 |
| 13 | Q-a1 | 1013.2 | 273.0 | 257.5 | 269.1 | 212.1 |
| 14 | K-a3 | 1037.2 | 255.0 | 261.7 | 254.6 | 264.5 |
| 15 | Q-r4 | 984.8 | 252.1 | 266.9 | 218.6 | 245.6 |
| 16 | K-r3 | 1012.4 | 264.8 | 233.7 | 260.9 | 251.6 |

| Appointment | Wall (min) |
|---|---|
| 1 | 66.4 |
| 2 | 66.1 |
| 3 | 68.2 |
| 4 | 67.5 |

## Effort

| Book | Method | Startup (min) | Operator (min) | Rater time (min) | Design active (min) | Familiarization (min) | Model runtime (min) | Tokens in | Tokens out |
|---|---|---|---|---|---|---|---|---|---|
| DEMO-BK-7QX4 | A1 | 0.0 | 0.0 | 193.0 | 73.0 | 0.0 | - | - | - |
| DEMO-BK-H9TC | A3 | 0.1 | 0.0 | 193.0 | - | - | 10.3 | 629814 | 11619 |
| DEMO-BK-M2RW | A2 | 0.0 | 0.0 | 193.0 | - | - | - | - | - |

Machines: not supplied (`--machines`).

## Diversity and durations

| Book | Candidate diversity | Committed diversity | 450 ms | 600 ms | 750 ms | 900 ms | Message min (ms) | Message max (ms) | Outside 1,100-2,000 ms |
|---|---|---|---|---|---|---|---|---|---|
| DEMO-BK-7QX4 | 0.475298 | 0.458574 | 4 | 3 | 6 | 3 | 1100 | 2000 | 0 |
| DEMO-BK-H9TC | 0.476896 | 0.464498 | 5 | 1 | 5 | 5 | 1100 | 2000 | 0 |
| DEMO-BK-M2RW | 0.268136 | 0.432141 | 2 | 4 | 5 | 5 | 1250 | 2000 | 0 |

Diversity: mean pairwise 12-feature distance among the valid candidates of each atom (averaged over atoms with two or more), and among the committed atoms.

## Checks

- Slot refusals: 1; bank scans: 1.
- No problems: every count checked.

## Sources

| File | SHA-256 |
|---|---|
| config.json | f8edf48d4471a369589eae34e8a79fd69fc9a62d4a281705fcf263f26bb5d921 |
| logs/commits.jsonl | 2ac035f4d00446cadf0acfec24a23f3549f08d55658626e86da9b617b03ea036 |
| logs/decisions.jsonl | 81ff774c3fea37a8bdd0b17959f2aa20b125e71548e1e2bf96affe178af189d3 |
| logs/fallback-scans.jsonl | 0f1d90032ad7ab7aef412f3dea5f3307fcf87d951b9d9129f6ff27a2b7a917c4 |
| logs/ratings.jsonl | 3a41f6c88efc58172cb69a696b32743dd8aeb931b229502467363397542a0b95 |
| logs/slot-refusals.jsonl | ffc6e0c88a666d67d00dc0b1bdfd94c8f3370c9567cde5ab22c9f87675d7bb49 |
| logs/slots.jsonl | fd74e56f2236ac6fbc2b9cf87df99776bbdf3efaaa4ca571758e4a256e2318cc |
| logs/timing.jsonl | 7f98d9921de5c5b13f168f15f47d26343956b73cd3970f0c37ffaeb0ed3d91a4 |
| run-manifest.json | fa2a0fc697de5bd3dd5774f9a7f088c7726fe5ac549761daf7201debe39ebe9a |
