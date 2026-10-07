# Generation audit: batch DEMO-A-P01

Run `DEMO-AUDIT-01` (synthetic); complete. Masked report: anonymous book IDs only, no column that names or reveals a method by construction.

## Books

| Book | Slots | Valid | Invalid | Selector | Bank fallback | Book fallback | Failed generation | Nonfallback |
|---|---|---|---|---|---|---|---|---|
| DEMO-BK-7QX4 | 192 | 153 | 39 | 0 | 0 | 16 | 1 | 0 |
| DEMO-BK-H9TC | 192 | 111 | 81 | 16 | 0 | 0 | 0 | 1 |
| DEMO-BK-M2RW | 192 | 139 | 53 | 15 | 1 | 0 | 0 | 0 |

Valid slots per round (all atoms):

| Book | Round 1 | Round 2 | Round 3 | Round 4 |
|---|---|---|---|---|
| DEMO-BK-7QX4 | 40 | 36 | 37 | 40 |
| DEMO-BK-H9TC | 26 | 26 | 30 | 29 |
| DEMO-BK-M2RW | 30 | 35 | 35 | 39 |

## Atoms

Valid slots of 12 and the committed source per book:

| # | Atom | Appointment | DEMO-BK-7QX4 | DEMO-BK-H9TC | DEMO-BK-M2RW |
|---|---|---|---|---|---|
| 1 | Q-a2 | 1 | 10 / fallback_book (voided: selector) | 6 / selector | 8 / selector |
| 2 | K-a4 | 1 | 8 / fallback_book (voided: selector) | 3 / selector | 6 / selector |
| 3 | Q-r2 | 1 | 10 / fallback_book (voided: selector) | 7 / selector | 8 / fallback_bank |
| 4 | K-r4 | 1 | 10 / fallback_book (voided: selector) | 9 / selector | 8 / selector |
| 5 | Q-a3 | 2 | 11 / fallback_book (voided: selector) | 8 / selector | 10 / selector |
| 6 | K-a1 | 2 | 9 / fallback_book (voided: selector) | 3 / selector | 9 / selector |
| 7 | Q-r1 | 2 | 12 / fallback_book | 8 / selector | 8 / selector |
| 8 | K-r1 | 2 | 10 / fallback_book | 7 / selector | 9 / selector |
| 9 | Q-a4 | 3 | 10 / fallback_book | 7 / selector | 10 / selector |
| 10 | K-a2 | 3 | 10 / fallback_book | 4 / selector | 7 / selector |
| 11 | Q-r3 | 3 | 6 / fallback_book | 7 / selector | 11 / selector |
| 12 | K-r2 | 3 | 10 / fallback_book | 8 / selector | 8 / selector |
| 13 | Q-a1 | 4 | 10 / fallback_book | 9 / selector | 11 / selector |
| 14 | K-a3 | 4 | 7 / fallback_book | 10 / selector | 8 / selector |
| 15 | Q-r4 | 4 | 10 / fallback_book | 7 / selector | 9 / selector |
| 16 | K-r3 | 4 | 10 / fallback_book | 8 / selector | 9 / selector |

## Wall time

Batch generation wall time (sum of the 16 atoms): 275.4 min; startup (all components): 3.4 min; operator actions: 3.2 min.

| # | Atom | Atom (s) | Round 1 (s) | Round 2 (s) | Round 3 (s) | Round 4 (s) |
|---|---|---|---|---|---|---|
| 1 | Q-a2 | 1037.6 | 254.7 | 276.2 | 256.2 | 250.5 |
| 2 | K-a4 | 1077.6 | 273.7 | 271.1 | 284.2 | 248.6 |
| 3 | Q-r2 | 968.4 | 234.9 | 229.9 | 243.5 | 260.1 |
| 4 | K-r4 | 1008.3 | 242.8 | 252.1 | 262.7 | 250.7 |
| 5 | Q-a3 | 1068.0 | 265.0 | 270.8 | 275.4 | 256.9 |
| 6 | K-a1 | 996.1 | 266.5 | 234.9 | 246.3 | 248.5 |
| 7 | Q-r1 | 994.7 | 252.3 | 235.6 | 261.5 | 245.3 |
| 8 | K-r1 | 1004.6 | 256.9 | 261.8 | 242.4 | 243.4 |
| 9 | Q-a4 | 1064.3 | 271.9 | 281.4 | 242.7 | 268.2 |
| 10 | K-a2 | 1037.0 | 272.2 | 253.6 | 259.7 | 251.4 |
| 11 | Q-r3 | 1096.3 | 269.9 | 282.3 | 281.0 | 263.1 |
| 12 | K-r2 | 1033.7 | 245.3 | 264.0 | 275.9 | 248.6 |
| 13 | Q-a1 | 994.3 | 240.9 | 271.8 | 242.0 | 239.6 |
| 14 | K-a3 | 1037.2 | 280.8 | 243.7 | 239.4 | 273.3 |
| 15 | Q-r4 | 1088.5 | 255.7 | 278.5 | 282.1 | 272.3 |
| 16 | K-r3 | 1020.0 | 272.4 | 259.0 | 245.0 | 243.7 |

| Appointment | Wall (min) |
|---|---|
| 1 | 70.2 |
| 2 | 69.7 |
| 3 | 72.5 |
| 4 | 71.0 |

## Effort

| Book | Wall (min) | Rater time (min) |
|---|---|---|
| DEMO-BK-7QX4 | 275.4 | 192.0 |
| DEMO-BK-H9TC | 275.4 | 192.0 |
| DEMO-BK-M2RW | 275.4 | 192.0 |

## Diversity and durations

| Book | Candidate diversity | Committed diversity | 450 ms | 600 ms | 750 ms | 900 ms | Message min (ms) | Message max (ms) | Outside 1,100-2,000 ms |
|---|---|---|---|---|---|---|---|---|---|
| DEMO-BK-7QX4 | 0.485171 | 0.501525 | 8 | 1 | 1 | 6 | 1100 | 2000 | 0 |
| DEMO-BK-H9TC | 0.471744 | 0.474417 | 6 | 1 | 4 | 5 | 1100 | 2000 | 0 |
| DEMO-BK-M2RW | 0.472390 | 0.476551 | 4 | 3 | 3 | 6 | 1100 | 2000 | 0 |

Diversity: mean pairwise 12-feature distance among the valid candidates of each atom (averaged over atoms with two or more), and among the committed atoms.

## Checks

- Slot refusals: 0; bank scans: 2.
- No problems: every count checked.

## Sources

| File | SHA-256 |
|---|---|
| config.json | 4744be455137c8ab7424eb956cfcd2b2839f6371e5b9100b16765c971bb65bc7 |
| logs/commits.jsonl | de856f2dc85aae900f9bbd209be268820dbb190faec6d0b5877d291e1a58591e |
| logs/decisions.jsonl | 531832d552dc32f99c0bfb76289157ed25c0352cc91229281a727fb86a35f830 |
| logs/fallback-scans.jsonl | ed9a11e77c41807ca5af4e07aa708b4b339b26851978eafc2d9b87b503fc8a02 |
| logs/ratings.jsonl | fec004bfc8ecc4aa0333a94b830898a60e02cde5e2339728a2dc4e2688eefe1d |
| logs/slots.jsonl | 798aed0168318a4336a826536d662c16d670dffa5b774d43ebd3e749527e7c55 |
| logs/timing.jsonl | 38f10e4773c7f89d79a65503cc04c5e84d52b29864206bda3eb97802877ddae8 |
| run-manifest.json | d9ee35c2dde42d717d7387125e521a9dd43a53b1e960ec5df62150092cfd86ef |
