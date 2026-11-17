# Synthetic whole-set dry run `DEMO-set-dry-run-18`

SYNTHETIC. bot raters, a scripted bot designer and a fake model; DEMO IDs only. Counts and fallback rates describe the stand-ins, not people.

| Quantity | Value |
| --- | ---: |
| Batches (P1 / P2 / P3) | 18 (6 / 6 / 6) |
| Panel sessions | 72 |
| Proposals | 10368 |
| Rating-slot records (per rater per batch) | 31104 (576) |
| Rater-candidate evaluations rated | 28053 |
| Placeholder records (invalid candidates, not rated) | 3051 |
| Missing ratings | 0 |
| Decisions | 3456 |
| Commits in final books | 864 |
| Panel orders 1-6 | 3 / 3 / 3 / 3 / 3 / 3 |
| Message plays, slot refusals | 0, 0 |
| Simulation wall time | 108 s |

| Method | Proposals | Valid | Invalid (outcome codes) | Selector commits | Bank fallbacks (per atom) | Whole-book fallbacks (per book) |
| --- | ---: | ---: | --- | ---: | ---: | ---: |
| A1 | 3456 | 2885 | 571 (event_too_short 283, invalid_json 144, timeout 144) | 288 | 0 (0.00%) | 0 (0.00%) |
| A2 | 3456 | 3315 | 141 (event_too_short 141) | 288 | 0 (0.00%) | 0 (0.00%) |
| A3 | 3456 | 3151 | 305 (event_too_short 304, separation_fail 1) | 288 | 0 (0.00%) | 0 (0.00%) |

Projected human time from the protocol budget (Study A §3.3):

| Quantity | Value |
| --- | ---: |
| Panel sessions | 72 |
| Booked panel hours (90 min per session) | 108 |
| Rater hours booked (3 raters per panel) | 324 |
| Maximum slot time (20 min per atom, 80 min per session) | 96 h |

Checks: every batch complete.
