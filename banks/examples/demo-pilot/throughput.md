# Pilot bank throughput (DEMO)

**DEMO rehearsal: fake model, synthetic latencies; not pilot data.**

- Plan `DEMO-pilot-banks`: 8 dyad slots, spare budget 2 (1 used); builder av-banks 0.1.0.
- Generation config `DEMO-gen-banks`, hash `514edf9a4baf3c6f564bd48c3d6fdbf7c6572714bcef68e53ce08f5a13c4c98d`, separation threshold 0.10.
- Register `pilot-register.csv` SHA-256 `afbd8ae442efbdf2824d59e332f37848357fc3e90f34c5c46b5ed7d7c3da5c1d`.
- Unavailable banks: DEMO-bank-P006 v1.0.0.
- Every dyad slot has a usable bank (8 of 8).

## Banks and attempts

| Measure | Value |
| --- | --- |
| Banks | 9 (8 complete, 1 unavailable) |
| Attempts per bank | 1.444 (min 1, max 4, n 9); banks by attempts: 1: 7, 2: 1, 3: 0, 4: 1 |
| Attempts per complete bank | 1.125 (min 1, max 2, n 8) |
| Attempt failure rate | 38.5% (5 of 13) |

## Throughput

| Measure | Value |
| --- | --- |
| Slots | 2,565; per bank 285 (min 236, max 517, n 9) |
| Slots per complete attempt | 248.625 (min 236, max 260, n 8) |
| Slots per failed attempt | 115.2 (min 59, max 134, n 5) |
| Slots per hour (bank builder) | 1,052.4 (2.437 attempt hours) |
| Slots per hour (runs) | 1,052.4 (2.437 run hours) |
| Model latency | p50 2,101 ms, p95 3,466 ms, max 40,000 ms |
| Slot time (open to close) | p50 2,217 ms, p95 3,468 ms, max 40,000 ms; over the 40-s cap: 0 |

## Failures

| Measure | Value |
| --- | --- |
| Cell failure rate | 1.0% (5 failed, 477 complete); by profile: P1 0.5%, P2 2.6%, P3 0.0% |
| Slots per complete cell | 5.252 (min 4, max 10, n 477) |
| Slot outcomes | valid 1908 (74.4%), invalid_json 239 (9.3%), schema_violation 107 (4.2%), out_of_domain 86 (3.4%), duplicate 49 (1.9%), incompatible 1 (0.0%), overflow_output 92 (3.6%), timeout 83 (3.2%) |
| Model calls by status | ok 2298, timeout 83, overflow_output 92, server_error 92 |

## Projection for 72 banks

| Measure | Value |
| --- | --- |
| Attempts | 104 |
| Slots | 20,520 |
| Hours, one bank at a time | 19.5 |
| Hours at the measured run rate | 19.5 |
| Unavailable banks | 8 |

Rough planning figures from a small number of banks; definitions in `av_banks.metrics`.
