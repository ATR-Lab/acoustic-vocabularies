# Study B confirmatory banks: DEMO-cbanks-01

Decision: **ready**

| Banks | Complete | Unavailable |
| --- | ---: | ---: |
| Main (64) | 62 | 2 |
| Spares (8) | 7 | 1 |
| All (72) | 69 | 3 |

- Escalation rule: complete banks < 64: stop and escalate to the advisor before G5B. Complete: 69 of 64 needed; no escalation needed.
- Unavailable banks (never assigned; log each with `log_bank_unavailable` before the first reveal): DEMO-C007, DEMO-C030, DEMO-C070.
- Generation config: `c689f6cedf6b0e9dc8cb48d8307ad9b3e2965382245598a5e64be82f68663519`; G4 freeze manifest `68f3323b51f5e41c10e8e79b62aceb7b2d31e1da8bbcc740ddde84c791c54859` (draft, no tag); freeze tag checked in the repository: no; #25 freeze guard run: no.
- Register CSV: `b39524916bec3279d0f19bfb6a5d1235265e27d00a7450a7f5693408267e1265`; archive `DEMO-cbanks-01-banks.tar`: `c6f79f5a1348a0056c57ada83b7cca3f87675364ccf053658f4d2600d029fef0` (14142 files).
- Verification log: `7959ec8b601d67d0f12a8a4c9ac654afe96cbe565e69141a702dacb12eae455d`; timing log: `e25aefc1b7c0bf860668e98621acf3573b665f0efe0b658a76ebb3800d0060c5`.
- Slots used: 13395; crashed bank versions: 0.

Next: commit `register.csv` and `register.json` (`publish`), then check the commit time against the first confirmatory screening (`commit-check`).
