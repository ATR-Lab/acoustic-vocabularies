# Power check for the protocol freeze (#309)

At the protocol freeze each primary contrast needs at least 80% power to detect a
difference of 10 percentage points from zero, with the unit SDs and the loss informed by
the pilot. `av_analysis.power_check` simulates that check over a grid of sizes with two
engines and the same tests as the analysis plan, and writes a verdict per contrast.

* Study A, one primary test: the batch differences A3-A2, two-sided one-sample t at
  alpha .05. The pilot (3 batches) cannot estimate between-batch variation, so the
  planning check uses a pessimistic batch-difference SD of .18 (central .13).
* Study B, two primary contrasts per pair: choice C (active minus assigned whole score)
  and teaching S (structured minus dictionary within each member, averaged over the two
  members), each a two-sided one-sample t over complete pairs, combined by the two-test
  Holm rule (the smaller p at .025, then the larger at .05).

## Command

```bash
uv run --project analysis python -m av_analysis.power_check run \
  --config analysis/examples/power-check/planning.json \
  --out analysis/out/power-planning [--replicates N] [--quiet]
```

The module has its own entry point (`power_check/__main__.py`); `av-analysis` itself is
unchanged. Exit codes: 0 success, 2 invalid configuration or refused output directory.
The committed planning example (608 rows, 2,000 replicates per cell) takes about 80 s on
one core.

**Where outputs may go.** The rule of `paths.committable` applies: a directory inside a
git work tree that is not git-ignored is refused unless the configuration is the committed
planning example (compared as parsed JSON, so line endings do not matter). Git-ignored
folders such as `analysis/out/` count as outside, as for REAL data roots. The
pilot-informed configuration and its outputs live in restricted storage.

## Modules

| Module | Contents |
| --- | --- |
| `power_check` | package API (`load_config`, `run_config`, `verdict`, `write_outputs`, ...) and `SCHEMAS` (the configuration schema) |
| `power_check.config` | configuration dataclasses, `config_schema`, `parse_config`, `load_config`, `is_planning_example` |
| `power_check.engines` | `latent_scenario`, the logistic-normal and unit-normal replicate functions, `balanced_structured`, `batch_drop_probability`, `decide` |
| `power_check.run` | cells and seeds, `run_cell`, `run_config`, `verdict`, `power.csv` / `power.md` / `verdict.json` |
| `power_check.__main__` | the `run` command |

## Configuration

A JSON document validated by `analysis/schema/power-check-config.schema.json` (generated
from `power_check.config.config_schema`, checked by `av-analysis schemas`), then by checks a
schema cannot express: unique SD-source and scenario names, every cell target inside
(0, 1), and for each primary contrast at least one scenario whose true effect equals
`effect_pp`.

| Field | Meaning |
| --- | --- |
| `format`, `format_version` | `av-analysis/power-check-config`, 1 |
| `basis` | `planning` (the committed synthetic example) or `pilot` (pilot-informed; restricted storage) |
| `description` | free text |
| `seed` | DEMO- label (`seeds.rng`) |
| `replicates` | per cell, default 2,000; at least 10 (tests); a decision run uses 1,000-2,000 |
| `alpha`, `effect_pp`, `criterion` | .05, 10 (pp), .80 in the planning example |
| `studies.A` | `baseline`, `learners_per_book`, `sizes` (batches, multiples of 6), `loss` (per learner), `heterogeneity`, `scenarios` (`effect_pp: {"A3-A2": ...}`) |
| `studies.B` | `baseline`, `sizes` (pairs, even), `loss` (per person), `heterogeneity`, `scenarios` (`effect_pp: {"C": ..., "S": ...}`) |

A `heterogeneity` entry (SD source) fixes the engine:

* `{"name", "engine": "logistic-normal", "latent": "central" | "pessimistic", "multiplier"}`:
  the latent logit SD set of the planning model (`simulate.A_CENTRAL_SD`, ...), every SD
  times `multiplier` (default 1; heterogeneity stress);
* `{"name", "engine": "unit-normal", "unit_sd": {...}, "correlation"}`: SDs of the unit
  differences on the proportion scale, `{"A3-A2": sd}` or `{"C": sd, "S": sd}`, and for
  Study B the correlation of a pair's C and S (default 0). This is the form the pilot
  summaries fill in.

`analysis/examples/power-check/planning.json` holds the planning values: Study A batches
18, 24, 30, 36, learner loss 5%, 4 learners per book, latent central and pessimistic, unit
SDs .13 and .18, scenarios effect (10 pp) and null; Study B pairs 64, 72, 80, 84, 88, 96,
person loss 15% and 25%, latent central and pessimistic, unit SDs C .18/.22 with S
.12/.15 (correlation 0), scenarios teaching present (C = S = 10 pp), teaching absent
(C = 10, S = 0) and global null. The pilot-informed configuration is written in February
from the pilot summaries (SDs, loss) and is kept in restricted storage, not here.

## Engines

Both engines return the unit differences of one replicate (NaN for an incomplete unit);
`engines.decide` applies `estimators.one_sample_t` to the complete units and, for Study B,
`estimators.holm` over C and S (if one test is unavailable, fewer than 2 complete pairs,
neither contrast is rejected, as in `simulate.dataset_results`). Study B also reports
`any` (C or S rejected; the family-wise rate under the global null) and `both`.

**Logistic-normal** (the planning trial model) calls `simulate.draw_a` / `draw_b` and
`a_differences` / `b_differences` with a `simulate.Scenario` per cell
(`engines.latent_scenario`): 18 items x 2 repetitions; cell targets Study A A1 = A2 =
baseline, A3 = baseline + effect; Study B assigned/dictionary baseline, assigned/structured
baseline + S, active/dictionary baseline + C, active/structured baseline + C + S; each
cell's intercept by `simulate.intercept` over the cell's total latent variance (Study B
slope variances count one quarter); `fault_rate = late_rate = 0`; the loss as
`attrition`. Sizes are free: `draw_a(units, learners)` and `draw_b(structured)` take any
number of batches or pairs, and Study B families are exactly balanced
(`engines.balanced_structured`: the first half of the pairs have the first family of 9
trained items structured, the second half the other family).

Semantics checked against what the power check needs (no difference found, so
`simulate.py` is unchanged and its outputs stay byte-identical):

| Requirement | `draw_a` / `draw_b` |
| --- | --- |
| item terms redrawn per replicate | yes: every call draws the item term (shared by all batches and books, or all pairs, within the replicate) |
| both repetitions share every latent term | yes: the two responses are Bernoulli draws on the same latent logit |
| loss MCAR, independent per person | yes: `missing` is an independent uniform per learner or member |
| Study A batch drops only if its A2 or A3 book has no learner left | yes: `a_differences` averages the remaining learners per book; the A1 book does not enter A3-A2 |
| Study B pair counts only if both members remain | yes: `b_differences` is NaN unless both are available |
| no faults, no late visits | `fault_rate = late_rate = 0` give all-False draws; they come after the response draws, so outcomes are unaffected (partial withdrawals only matter for missing endpoints, which are dropped either way) |
| any size, balanced families | yes (above); only `dataset_results` binds to the DEMO allocations (whose 64 pairs are also balanced 32/32) |
| multiplier | applied to the SDs before squaring, through `simulate._variances` (multiplier 1 gives exactly the variances of the `central-A` ... `pessimistic-B` scenarios) |

**Unit-normal** draws the unit differences directly. Study A: batch differences
`Normal(effect, sd)`; a batch drops with probability `1 - (1 - q^L)^2` (its A2 or A3 book
loses all `L` learners at learner loss `q`); the unit SD is taken as given whatever the
number of remaining learners (a pilot SD already includes that). Study B: `(C, S)`
bivariate normal with SDs `(sd_C, sd_S)` and correlation `rho`; a pair is complete only
if both members remain (probability `(1 - q)^2`).

## Seeds and outputs

Replicate `k` of a cell (study x SD source x scenario x loss x size) draws from
`seeds.rng(seed, "power-check", study, source, scenario, "loss-<q>", "n-<size>", k)`:
cells are independent (no common random numbers across sizes, so neighbouring sizes can
cross by Monte Carlo noise) and the same configuration gives the same bytes. No wall-clock
time is written.

* `power.csv`: one row per cell and contrast: `data_kind` (SYNTHETIC), `basis`, `study`,
  `engine`, `sd_source`, `sd_setting`, `scenario`, `loss`, `size`, `contrast`,
  `true_effect_pp`, `rule`, `replicates`, `rejections`, `rate`, `mcse`
  (`sqrt(p(1-p)/n)`), `mc95_low`/`mc95_high` (Wilson 95% Monte Carlo interval),
  `mean_complete_units`, `implied_unit_sd` (mean over replicates of the SD of the complete
  unit differences) and `implied_unit_sd_mcse`, `mean_estimate`, `unavailable`, `seed`.
* `power.md`: the verdict table, power % (MCSE) per contrast by size, implied unit SDs.
* `verdict.json`: per primary contrast (A3-A2, C, S) and case (SD source x loss x
  scenario whose true effect for that contrast equals `effect_pp`; for C both teaching
  scenarios count), per size the rate, MCSE, Wilson limits, `holds` (point estimate >=
  criterion) and `holds_mc95_low` (lower limit >= criterion), the smallest grid size that
  reaches the criterion either way, and `all_cases`: the smallest size at which every case
  holds. **The verdict uses the point estimate**; the Monte Carlo lower limit is shown
  next to it. `decision_grade` is false below 1,000 replicates.

## Reproduction of the earlier planning simulation values

The logistic-normal engine on the committed planning example (seed
`DEMO-power-check-planning`, 2,000 replicates per cell) against the earlier planning
simulation values (4 learners per book, 18 items x 2 repetitions; Study A 5% learner
loss; Study B 15% person loss unless noted; 2,000 planning replicates unless noted).
`z = (ours - planning) / sqrt(MCSE_ours^2 + MCSE_planning^2)` with the planning MCSE
`sqrt(p(1-p)/n)`; |z| <= 2 counts as reproduced. The last column is the unit-normal
engine at unit SDs .13/.18 (A) and C .18/.22 with S .12/.15 (B), same cell.

| Row | Size | Planning | Ours | MCSE | z | Unit-normal |
| --- | --- | --- | --- | --- | --- | --- |
| A central, A3-A2 | 18 | 85.2% | 85.9% | 0.8 | +0.58 | 86.7% |
| A pessimistic | 18 | 60.4% | 59.5% | 1.1 | -0.61 | 60.2% |
| A pessimistic (planning 1,000 reps) | 24 | 70.9% | 74.2% | 1.0 | +1.93 | 74.4% |
| A pessimistic (planning 1,000 reps) | 30 | 84.8% | 82.5% | 0.8 | -1.59 | 83.2% |
| A pessimistic (planning 1,000 reps) | 36 | 89.5% | 89.2% | 0.7 | -0.21 | 89.6% |
| A null (effect 0), central | 18 | 4.6% | 5.2% | 0.5 | +0.88 | 4.2% |
| B central, both effects, C | 64 | 95.2% | 94.9% | 0.5 | -0.44 | 95.9% |
| B central, both effects, S | 64 | 100.0% | 100.0% | 0.0 | 0.00 | 99.9% |
| B pessimistic, both effects, C | 64 | 83.2% | 86.4% | 0.8 | **+2.78** | 85.4% |
| B pessimistic, both effects, S | 64 | 99.6% | 99.3% | 0.2 | -1.28 | 99.0% |
| B pessimistic, both, 25% loss, C | 64 | 73.7% | 71.2% | 1.0 | -1.74 | 73.3% |
| B pessimistic, both, 25% loss, S | 64 | 96.9% | 96.9% | 0.4 | 0.00 | 95.7% |
| B pessimistic, both, C (planning 1,000 reps) | 80 | 92.7% | 91.9% | 0.6 | -0.78 | 92.1% |
| B pessimistic, both, 25% loss, C (planning 1,000 reps) | 80 | 84.1% | 84.4% | 0.8 | +0.21 | 83.7% |
| B central, no teaching effect, C | 64 | 85.7% | 84.9% | 0.8 | -0.76 | 93.0% |
| B pessimistic, no teaching effect, C | 64 | 68.4% | 69.0% | 1.0 | +0.44 | 77.8% |
| B pessimistic, no teaching effect, C | 80 | 79.9% | 77.4% | 0.9 | -1.93 | 88.2% |
| B pessimistic, no teaching effect, C | 84 | 81.4% | 81.8% | 0.9 | +0.33 | 88.8% |
| B pessimistic, no teaching effect, C | 88 | 82.8% | 82.8% | 0.8 | 0.00 | 89.5% |
| B global null, central, any rejection | 64 | 4.2% | 4.2% | 0.4 | 0.00 | 4.9% |

Mean implied unit SD (A at 18 batches; B at 64 pairs, both effects, 15% loss). The
planning values are rounded to .001 and their MCSE is unknown; z uses our MCSE for both
runs plus the rounding variance (.001^2 / 12).

| Row | Planning | Ours (MCSE) | z | Unit-normal |
| --- | --- | --- | --- | --- |
| A central / pessimistic | .131 / .180 | .1298 (.0005) / .1788 (.0007) | -1.62 / -1.21 | .1289 / .1775 |
| C central / pessimistic | .182 / .222 | .1821 (.0005) / .2231 (.0006) | +0.14 / +1.20 | .1788 / .2190 |
| S central / pessimistic | .120 / .147 | .1194 (.0003) / .1484 (.0004) | -1.06 / **+2.17** | .1194 / .1497 |

24 of the 26 rows reproduce at |z| <= 2; all 26 are within |z| <= 3. The two others:
Study B pessimistic, both effects, C at 64 pairs (86.4% against 83.2%) and the S
pessimistic implied SD (.1484 against .147). No semantic difference explains them: a
diagnostic run of the same cells with another seed label and 10,000 replicates (not
committed) gave 85.2% (MCSE 0.4) and .1476, which lie between the two values (planning
z -2.1, ours +1.4 for the rate). The likely explanation is Monte Carlo variation in both
simulations. The model and the seed were not tuned.

The unit-normal engine agrees with the logistic-normal one where both effects are present
(its unit SDs are the implied SDs of that scenario). With no teaching effect it gives
clearly higher C power: in the logistic model C's implied SD grows when S = 0 (the
structured cells sit at .65/.75 instead of .75/.85, nearer .5, where the latent terms and
the response noise move the proportions more; at 64 pairs and 15% loss the implied C SD
is .199 rather than .182 (central) and .243 rather than .223 (pessimistic)), which a
fixed unit SD does not follow. A pilot-informed unit SD for C should therefore match the
scenario it is used in.

With the planning example the verdict is: A3-A2 reaches 80% at 30 batches in every case
(pessimistic 82.5%); S at 64 pairs in every case; C at 64 to 88 pairs except in the
logistic pessimistic, no-teaching, 25%-loss case, which stays below 80% up to 96 pairs
(75.5%), so `all_cases` for C is "not reached".

Rerun the rows above (about 15 s):

```bash
AV_POWER_FULL=1 uv run --project analysis pytest --import-mode=importlib \
  -p no:cacheprovider tests/analysis/test_power_check_reproduction.py -s
```

## Decisions (Proposed)

* The verdict uses the point estimate of power against the criterion; the Wilson 95% Monte
  Carlo lower limit is reported beside it (`smallest_size_mc95_low`), not used.
* "Smallest size" is the first grid size that reaches the criterion; per-size flags show
  any non-monotone neighbour. `all_cases` requires every listed case, so the
  configuration should list exactly the cases the freeze must satisfy; C counts under
  both teaching scenarios (C needs alpha/2 more often when S is absent).
* Study A sizes are multiples of 6 and Study B sizes even (exact family balance).
* With one Study B test unavailable, neither contrast is rejected (as `simulate`).
* Outputs are marked SYNTHETIC and seeds are DEMO- labels for both bases; a pilot-based
  run embeds pilot-derived SDs, so it stays in restricted storage with its configuration.
* Fewer than 1,000 replicates: `decision_grade: false`.
* Independent random streams per cell (no common random numbers across sizes).
