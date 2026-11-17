# Synthetic whole-set dry run `DEMO-set-dry-run-30`

SYNTHETIC. bot raters, a scripted bot designer and a fake model; DEMO IDs only. Counts and fallback rates describe the stand-ins, not people.

| Quantity | Value |
| --- | ---: |
| Batches (P1 / P2 / P3) | 30 (10 / 10 / 10) |
| Panel sessions | 120 |
| Proposals | 17280 |
| Rating-slot records (per rater per batch) | 51840 (576) |
| Rater-candidate evaluations rated | 46785 |
| Placeholder records (invalid candidates, not rated) | 5055 |
| Missing ratings | 0 |
| Decisions | 5760 |
| Commits in final books | 1440 |
| Panel orders 1-6 | 5 / 5 / 5 / 5 / 5 / 5 |
| Message plays, slot refusals | 0, 0 |
| Simulation wall time | 177 s |

| Method | Proposals | Valid | Invalid (outcome codes) | Selector commits | Bank fallbacks (per atom) | Whole-book fallbacks (per book) |
| --- | ---: | ---: | --- | ---: | ---: | ---: |
| A1 | 5760 | 4832 | 928 (event_too_short 447, invalid_json 240, separation_fail 1, timeout 240) | 480 | 0 (0.00%) | 0 (0.00%) |
| A2 | 5760 | 5516 | 244 (event_too_short 244) | 480 | 0 (0.00%) | 0 (0.00%) |
| A3 | 5760 | 5247 | 513 (event_too_short 513) | 480 | 0 (0.00%) | 0 (0.00%) |

Projected human time from the protocol budget (Study A §3.3):

| Quantity | Value |
| --- | ---: |
| Panel sessions | 120 |
| Booked panel hours (90 min per session) | 180 |
| Rater hours booked (3 raters per panel) | 540 |
| Maximum slot time (20 min per atom, 80 min per session) | 160 h |

Checks: every batch complete.
