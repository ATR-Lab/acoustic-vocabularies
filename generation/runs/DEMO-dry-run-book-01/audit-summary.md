# Generation audit: batch DEMO-DRY-P01

Run `DEMO-dry-run-book-01` (synthetic); complete. Unmasked report: names methods. Restricted: generation operator only; never give it to session experimenters or blinded analysts.

## Books

| Book | Method | Designer | Slots | Valid | Invalid | Selector | Bank fallback | Book fallback | Failed generation | Nonfallback |
|---|---|---|---|---|---|---|---|---|---|---|
| DEMO-BK-7QX4 | A1 | D1 | 192 | 155 | 37 | 16 | 0 | 0 | 0 | 1 |
| DEMO-BK-H9TC | A3 | - | 192 | 175 | 17 | 0 | 0 | 16 | 1 | 0 |
| DEMO-BK-M2RW | A2 | - | 192 | 181 | 11 | 16 | 0 | 0 | 0 | 1 |

Valid slots per round (all atoms):

| Book | Round 1 | Round 2 | Round 3 | Round 4 |
|---|---|---|---|---|
| DEMO-BK-7QX4 | 41 | 39 | 36 | 39 |
| DEMO-BK-H9TC | 42 | 45 | 44 | 44 |
| DEMO-BK-M2RW | 41 | 48 | 47 | 45 |

## Slot outcomes by code

All rounds:

| Outcome | DEMO-BK-7QX4 | DEMO-BK-H9TC | DEMO-BK-M2RW |
|---|---|---|---|
| valid | 155 | 175 | 181 |
| invalid_json | 8 | 0 | 0 |
| schema_violation | 0 | 0 | 0 |
| out_of_domain | 0 | 0 | 0 |
| render_fail | 0 | 0 | 0 |
| event_too_short | 20 | 17 | 11 |
| clipping | 0 | 0 | 0 |
| duplicate | 0 | 0 | 0 |
| reserved_collision | 0 | 0 | 0 |
| separation_fail | 1 | 0 | 0 |
| incompatible | 0 | 0 | 0 |
| overflow_input | 0 | 0 | 0 |
| overflow_output | 0 | 0 | 0 |
| timeout | 8 | 0 | 0 |
| llm_server_error | 0 | 0 | 0 |

Per round, book DEMO-BK-7QX4 (A1):

| Outcome | Round 1 | Round 2 | Round 3 | Round 4 |
|---|---|---|---|---|
| valid | 41 | 39 | 36 | 39 |
| invalid_json | 2 | 3 | 0 | 3 |
| event_too_short | 4 | 4 | 10 | 2 |
| separation_fail | 0 | 1 | 0 | 0 |
| timeout | 1 | 1 | 2 | 4 |

Per round, book DEMO-BK-H9TC (A3):

| Outcome | Round 1 | Round 2 | Round 3 | Round 4 |
|---|---|---|---|---|
| valid | 42 | 45 | 44 | 44 |
| event_too_short | 6 | 3 | 4 | 4 |

Per round, book DEMO-BK-M2RW (A2):

| Outcome | Round 1 | Round 2 | Round 3 | Round 4 |
|---|---|---|---|---|
| valid | 41 | 48 | 47 | 45 |
| event_too_short | 7 | 0 | 1 | 3 |

## Atoms

Valid slots of 12 and the committed source per book:

| # | Atom | Appointment | DEMO-BK-7QX4 | DEMO-BK-H9TC | DEMO-BK-M2RW |
|---|---|---|---|---|---|
| 1 | Q-a2 | 1 | 9 / selector | 10 / fallback_book (voided: fallback_bank) | 12 / selector |
| 2 | K-a4 | 1 | 11 / selector | 11 / fallback_book | 12 / selector |
| 3 | Q-r2 | 1 | 9 / selector | 10 / fallback_book | 11 / selector |
| 4 | K-r4 | 1 | 11 / selector | 12 / fallback_book | 11 / selector |
| 5 | Q-a3 | 2 | 10 / selector | 11 / fallback_book | 12 / selector |
| 6 | K-a1 | 2 | 9 / selector | 12 / fallback_book | 12 / selector |
| 7 | Q-r1 | 2 | 10 / selector | 11 / fallback_book | 12 / selector |
| 8 | K-r1 | 2 | 10 / selector | 11 / fallback_book | 12 / selector |
| 9 | Q-a4 | 3 | 11 / selector | 11 / fallback_book | 11 / selector |
| 10 | K-a2 | 3 | 7 / selector | 11 / fallback_book | 11 / selector |
| 11 | Q-r3 | 3 | 12 / selector | 9 / fallback_book | 10 / selector |
| 12 | K-r2 | 3 | 9 / selector | 12 / fallback_book | 11 / selector |
| 13 | Q-a1 | 4 | 10 / selector | 11 / fallback_book | 11 / selector |
| 14 | K-a3 | 4 | 9 / selector | 11 / fallback_book | 12 / selector |
| 15 | Q-r4 | 4 | 9 / selector | 11 / fallback_book | 10 / selector |
| 16 | K-r3 | 4 | 9 / selector | 11 / fallback_book | 11 / selector |

## Wall time

Batch generation wall time (sum of the 16 atoms): 269.9 min; startup (all components): 0.1 min; operator actions: 0.0 min.

| # | Atom | Atom (s) | Round 1 (s) | Round 2 (s) | Round 3 (s) | Round 4 (s) |
|---|---|---|---|---|---|---|
| 1 | Q-a2 | 1011.0 | 235.0 | 273.0 | 249.7 | 252.2 |
| 2 | K-a4 | 1000.4 | 261.6 | 217.8 | 257.3 | 248.5 |
| 3 | Q-r2 | 1042.8 | 259.1 | 266.5 | 247.3 | 268.8 |
| 4 | K-r4 | 1022.9 | 233.6 | 263.5 | 253.0 | 271.9 |
| 5 | Q-a3 | 999.1 | 276.8 | 255.3 | 241.9 | 223.7 |
| 6 | K-a1 | 985.8 | 249.9 | 248.5 | 227.3 | 258.5 |
| 7 | Q-r1 | 1025.2 | 263.0 | 258.5 | 252.2 | 250.0 |
| 8 | K-r1 | 979.0 | 256.6 | 221.2 | 248.8 | 250.7 |
| 9 | Q-a4 | 1022.8 | 266.2 | 229.1 | 269.2 | 256.5 |
| 10 | K-a2 | 950.9 | 225.1 | 248.3 | 241.6 | 234.1 |
| 11 | Q-r3 | 1024.6 | 259.6 | 244.8 | 257.5 | 260.8 |
| 12 | K-r2 | 1018.9 | 226.7 | 269.1 | 261.8 | 258.7 |
| 13 | Q-a1 | 1008.5 | 278.7 | 244.9 | 244.3 | 238.4 |
| 14 | K-a3 | 1042.1 | 261.8 | 253.8 | 259.8 | 264.4 |
| 15 | Q-r4 | 1047.3 | 254.6 | 246.3 | 282.4 | 261.4 |
| 16 | K-r3 | 1013.7 | 279.2 | 218.6 | 246.5 | 266.7 |

| Appointment | Wall (min) |
|---|---|
| 1 | 68.0 |
| 2 | 66.5 |
| 3 | 67.0 |
| 4 | 68.5 |

## Effort

| Book | Method | Startup (min) | Operator (min) | Rater time (min) | Design active (min) | Familiarization (min) | Model runtime (min) | Tokens in | Tokens out |
|---|---|---|---|---|---|---|---|---|---|
| DEMO-BK-7QX4 | A1 | 0.0 | 0.0 | 192.8 | 73.7 | 0.0 | - | - | - |
| DEMO-BK-H9TC | A3 | 0.1 | 0.0 | 193.2 | - | - | 10.8 | 602002 | 11600 |
| DEMO-BK-M2RW | A2 | 0.0 | 0.0 | 192.8 | - | - | - | - | - |

Machines: not supplied (`--machines`).

## Diversity and durations

| Book | Candidate diversity | Committed diversity | 450 ms | 600 ms | 750 ms | 900 ms | Message min (ms) | Message max (ms) | Outside 1,100-2,000 ms |
|---|---|---|---|---|---|---|---|---|---|
| DEMO-BK-7QX4 | 0.470994 | 0.469877 | 4 | 3 | 1 | 8 | 1100 | 2000 | 0 |
| DEMO-BK-H9TC | 0.472877 | 0.501525 | 8 | 1 | 1 | 6 | 1100 | 2000 | 0 |
| DEMO-BK-M2RW | 0.258436 | 0.469137 | 3 | 5 | 2 | 6 | 1250 | 2000 | 0 |

Diversity: mean pairwise 12-feature distance among the valid candidates of each atom (averaged over atoms with two or more), and among the committed atoms.

## Checks

- Slot refusals: 0; bank scans: 2.
- No problems: every count checked.

## Sources

| File | SHA-256 |
|---|---|
| config.json | dee11ea25107a27d985c2897cff1a0189301fa2bf3eaeb122e36abfe8a7cac6e |
| logs/commits.jsonl | 6edc93155c2cac44b6059f559d1ee3ac970e19e46f6b18b87486004b2c04a321 |
| logs/decisions.jsonl | 0e1f095f95db4c43fb6405d97d189b8c97e4002da9ed2a28238c02ebb3fe7f1f |
| logs/fallback-scans.jsonl | 874fa5a444ce4f6ddaba4145523d879d843bcb16c3f91b89c5067ce9aee40c45 |
| logs/ratings.jsonl | 42fc164c530b2da310b1476bf14ad1ae8003e8ef74aa10deeac49d1ca0803346 |
| logs/slots.jsonl | 34d7a37072c601c370532befd9b40bd4d256becb00614bd28c38caa8c4fe0198 |
| logs/timing.jsonl | 6eb1f15652439f7abe72f07610c5ccc532cdd8c6bc2161c9a7943fdfef571ecd |
| run-manifest.json | f5a3fcb495751feac301a97bd1f7368611501906aaeeb84698f858039b33e10b |
