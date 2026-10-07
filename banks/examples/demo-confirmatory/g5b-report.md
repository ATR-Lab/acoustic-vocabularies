# Study B confirmatory banks: DEMO-cbanks-01

Decision: **ready**

| Banks | Complete | Unavailable |
| --- | ---: | ---: |
| Main (64) | 62 | 2 |
| Spares (8) | 7 | 1 |
| All (72) | 69 | 3 |

- Escalation rule: complete banks < 64: stop and escalate to the advisor before G5B. Complete: 69 of 64 needed; no escalation needed.
- Unavailable banks (never assigned; log each with `log_bank_unavailable` before the first reveal): DEMO-C007, DEMO-C030, DEMO-C070.
- Generation config: `c689f6cedf6b0e9dc8cb48d8307ad9b3e2965382245598a5e64be82f68663519` (G4 freeze `DEMO-g4-freeze`, manifest `ffe41c3baa70b2fd64544f07bdfe5eed4d8387fa0a7d57a914e37cacd902417f`).
- Register CSV: `b39524916bec3279d0f19bfb6a5d1235265e27d00a7450a7f5693408267e1265`; archive `DEMO-cbanks-01-banks.tar`: `6ea894699903c37f30cad1fb6653743d4de49e467307408de774cf96a971c146` (14142 files).
- Verification log: `7959ec8b601d67d0f12a8a4c9ac654afe96cbe565e69141a702dacb12eae455d`; timing log: `749e5cb8bbe50ee781ea21ef47edfc1a1cde626a60fe1ebc066ee523f98c8cb0`.
- Slots used: 13395; crashed bank versions: 0.

Next: commit `register.csv` and `register.json` (`publish`), then check the commit time against the first confirmatory screening (`commit-check`).
