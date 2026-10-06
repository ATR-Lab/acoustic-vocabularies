# Analysis pipeline (#34, O4.5.2)

The prespecified analysis of both studies, built and tested on synthetic data only:
scoring, the Study A batch-difference t test with Holm-adjusted A1 contrasts, the Study B
two-test Holm family, stratified bootstraps, supporting GLMMs with the fallback ladder,
all-assigned [0,1] bounds, tipping points, the synthetic data generator and the
analysis plan section 9 report. Shared rules (data roots, watermark, tables, masking,
R pins) are in [`architecture.md`](architecture.md); formats for other components are in
[`docs/interfaces/analysis.md`](../../docs/interfaces/analysis.md) ("Analysis pipeline
(#34)"). Protocol references: analysis plan sections 2-9, Study A protocol section 8,
Study B protocol sections 2, 7 and 10, Protocol constants ("Analysis and planning
boundary").

```sh
# Operating characteristics (2,000 synthetic datasets per scenario) into a SYNTHETIC root
uv run --project analysis av-analysis simulate --scenario null-A --datasets 2000 \
    --seed DEMO-o4.5.2-null --out <root>
# A full-size synthetic dataset (derived tables, key, lists) and its section 9 report
uv run --project analysis av-analysis simulate --scenario central-A --datasets 1 \
    --seed DEMO-local --out <root> --write-dataset
uv run --project analysis av-analysis run --study A --data <root> [--set S] [--glmm auto|require|skip]
```

`run` exit codes: 0 written, 2 refused input (see "Refusals"), 3 not implemented. Both
commands write only into the root they are given; `simulate` refuses REAL roots and
non-`DEMO-` seeds.

## 1. Modules

| Module | Contents |
| --- | --- |
| `scoring` | `score_trial`, `battery_scores`, `scored_opportunities` (`TrialScore`, `BatteryScore`, `ScoredTrial`) |
| `unmask` | `load_conditions` (`Conditions`, `BookCondition`, `DyadCondition`), `revealed_slots`; the only reader of `keys/` |
| `estimators` | `one_sample_t`, `holm`, `stratified_bootstrap`, `wilson`, `km_median`; aggregation `a_book_means`, `a_batch_differences`, `b_dyad_differences` |
| `missingness` | `all_assigned_bounds`, `tipping_grid`, `tipping_imputations`, `tipping_summary`, `person_interval` |
| `glmm` | `model_specs`, `model_data`, `fit_ladder` / `fit_with_ladder`, `not_run_log` (ladder order and log format are the skeleton's) |
| `rbridge` | `run_r`, `version_problems` (pins and `Rscript` lookup are the skeleton's); R script `analysis/r/glmm.R` |
| `simulate` | `scenarios`, `intercept`, `draw_a`, `draw_b`, `dataset_results`, `operating_characteristics`, `simulate_dataset`, the `simulate` command, schema `operating-characteristics-row.schema.json` |
| `synthetic_tables` | the table path of `simulate`: derived `trials` and `endpoints` rows built on the real schedule structure (`av_schedules`) |
| `report` | `ReportTable`, `SectionContent`, `StudyReport`, `render`, `write_report`, `write_dataset`, `write_simulation`, `update_manifest` (section order and `TableMeta` are the skeleton's) |
| `pipeline` | `load_study`, `analyze`, `run_analysis`, the `run` command |

## 2. Inputs and refusals

`run` reads `derived/trials.csv` and `derived/endpoints.csv` (#33 contract,
`derived.TRIALS`/`ENDPOINTS`) with the root's watermark, the allocation lists through
`unmask` (Study A `keys/A/<set>-book-key.json` and `inputs/schedules/A/<set>-slots.json`;
Study B `inputs/schedules/B/<set>-dyads.json`, all checked with their `list_sha256`),
the reveal log `inputs/reveal/<study>-<set>.jsonl` when it exists, and, when present,
`reconciled/visit-status.csv`, `reconciled/discrepancies.csv`,
`reconciled/enrollment.csv` (counts only) and `inputs/sound/golden-manifest.json`. Every
file read is listed with its SHA-256 in the run record and in `estimates/manifest.json`.

The set is `--set`, else the root's set, else the only set with rows. A run refuses (exit
2): tables of the other data kind; a held visit (accounted opportunities) whose
`reconciliation` is not `pass` (#33 marks a visit `pass` when every discrepancy is
explained by a deviation record); a person, unit or (Study A) book that differs from the
allocation lists; derived rows that cannot be scored (`scoring.ScoringError`); lists
whose `demo` flag differs from the root's kind; an edited list or reveal log; an
`enrollment` row whose `reveal_log_sha256` is not the reveal log read (stale: run
`av-analysis refresh`).

**Assigned persons** (the all-assigned denominator): the slots revealed in the reveal log
(Study B: both members of every revealed dyad slot, spares included), or every main-list
slot when there is no reveal log yet.

## 3. Scoring (analysis plan section 2)

* A **response opportunity** is a `trials` row of a test block (`pre_old`, `trained`,
  `novel`, `atomic`, `no_cue`, `speech`) that is not a linked retry. Its battery is its
  block; `endpoints.accounted_n` must equal the number of opportunities (else refused).
* **Y = 1** only for a first commit whose action and referent both equal the private
  target (atomic probes: the probed role). Wrong components, `dont_know`, `timeout` and an
  empty response code are 0.
* A **verified technical failure** (a `row_source` deviation row, or any fault code or
  fault type) is 0 operationally whatever response was logged, and keeps its codes.
* **Valid-delivery score**: rows with `valid_delivery` true only.
* **Battery score** = sum of Y / scheduled opportunities, only when the endpoints row is
  `complete`; a partial battery keeps `operational_sum` and `operational_n` for the
  bounds. Per-family scores (K, Q) use half the scheduled opportunities (18 of 36).
* **Time to commit** = commit minus audible onset (logged response time if either is
  missing), right-censored at 12 s (7 s for atomic probes); abstentions and timeouts are
  censored; no-cue trials and technical failures have no time. The report gives the
  Kaplan-Meier median and, labelled conditional, the median of correct commits.

## 4. Endpoints and estimators

The primary endpoint is the trained battery of Study A D0 and Study B W1 with
`planned_endpoint` true (complete, and in window or an anchor visit). Late visits enter
only the timing sensitivity.

**Study A** (`estimators`): `A[b,m]` = equal-weight mean of the complete learner scores
of each book (planned and contributing learners are reported per book); `D[b] =
A[b,A3] - A[b,A2]`; two-sided one-sample t test of the `B_eff` available differences
(df `B_eff - 1`), 95% t interval in percentage points, "unavailable" with fewer than 2.
Secondary A3-A1 and A2-A1 the same way, with Holm over their two p values, and by A1
designer (descriptive intervals). Bootstrap: 10,000 resamples of whole batches within
profile family, percentile interval; strata with fewer than 2 complete batches are listed
as inadequately supported.

**Study B**: `C[d]` = active minus yoked whole score; `S[d]` = mean over the two members
of structured-family minus dictionary-family score (18 opportunities each). Both members
need a complete in-window W1 endpoint. Two one-sample t tests, Holm at .05 (the smaller p
at .025, then the larger at .05), with labelled 95% and 97.5% intervals; whole-dyad
bootstraps within scaffold allocation x heldout-set order; `S[active] - S[yoked]` as an
exploratory row outside the family.

`one_sample_t` uses compensated sums and scipy's t distribution; tests check t, df, p and
both intervals against closed forms of the t distribution (df 1, 2 and 4) within 1e-10.
`holm` ranks by p (ties by name), stops at the first failure and reports the running-max
adjusted p. Bootstrap seeds are stored labels: `A-primary-bootstrap-v1`,
`B-primary-bootstrap-v1-C`, `-S` (prefixed `DEMO-` on SYNTHETIC roots), drawn with
`seeds.rng`.

**Secondary and diagnostic endpoints** (section 5 of the report): delayed trained
accuracy (A D7, B W4), first pass only, action and referent components (never averaged
into exact accuracy), novel first exposures (repeat rows excluded), atomic probes, the
no-cue and speech validity diagnostics; per condition and as unit-level contrasts with
unadjusted 95% intervals.

## 5. Missingness (analysis plan section 6)

* **Bounds** (`all_assigned_bounds`): a person's primary score lies in `[s/n, (s + n -
  k)/n]` for a partial battery (`s` correct of `k` accounted, `n` scheduled), `[0, 1]`
  without data, the score when complete. Book means over every assigned learner, batch
  differences (A) and dyad differences over every assigned pair (B) are monotone in each
  score, so the worst and best compatible point effects come from the interval ends.
  Study B uses per-family intervals and a whole score equal to their average, so C and S
  bounds rest on the same per-person values (Study B scores must carry both family
  scores). `persons_missing` counts the persons of the contrast without a complete
  endpoint: A3 and A2 learners (A1 plays no part in A3-A2), both members of every dyad.
* **Tipping grid** (`tipping_grid`), 21 x 21 cells per contrast in steps of .05; each
  cell's estimate is the all-assigned point estimate over every planned unit.
  `direction_changed`: the sign differs from the observed complete-unit estimate (zero
  counts as changed); `practical_changed`: "at least 10 points in favour" differs.
  `tipping_summary` reports the first cell (smallest total shift) of each.
  * Study A: missing A3 and A2 learners take the observed mean of their method, A3 moved
    down by `i` and A2 up by `j`, clipped to each person's interval.
  * Study B follows analysis plan section 6 ("do not use inconsistent imputed values for
    different estimands"): in every cell a missing person has one structured and one
    dictionary score, each clipped to its family interval, and a whole score equal to
    their average; C and S of the cell are computed from those same values. The
    reference is the observed mean of the person's role and family among complete
    persons, so without data a missing person's whole score starts at the observed mean of
    its role. The `C` grid moves both family scores of missing active members down by `i`
    and of missing yoked members up by `j`; the `S` grid moves every missing person's
    structured score down by `i` and dictionary score up by `j`. Both grids start from the
    same values, and each cell also reports the other contrast's estimate from its values
    (`TippingCell.companion`, columns `companion_contrast` and `companion_estimate_pp`).
    `tipping_imputations` returns the imputed values behind any cell.
* The valid-delivery, timing and available-observation analyses are in section 7 of the
  report; the non-fallback sensitivity waits for the generation audit tables (#24,
  **Pending**).

## 6. Supporting GLMMs

`glmm.model_specs`:

| Model | Data | Fixed | Random (full rung) |
| --- | --- | --- | --- |
| `A-trained` | trained trials, D0 and D7 | method (ref. A2), profile, repetition, family, endpoint | `(1 \| unit_id) + (1 \| book_id) + (1 \| person_id) + (1 \| item_id)` |
| `A-designer` | same | method x A1 designer (`A1-D1`..`A1-D3`), profile, repetition, family, endpoint | same |
| `B-trained` | trained trials, W1 and W4 | `role_c * format_c` (centred +/-0.5), family, repetition, visit | `(1 + role_c \| unit_id) + (1 + format_c \| person_id) + (1 \| item_id)` |

Model data (`model_data`, written as `estimates/glmm/<model>-data.csv`) holds every
in-window or anchor-visit opportunity of every assigned person with data, partial
batteries included (the available-observation model), with the operational Y.

**Ladder** (`fit_ladder`): `full` -> `no_correlations` (`||` on the centred codes) ->
`no_dyad_role_slope` -> `no_participant_teaching_slope` (intercepts only) ->
`descriptive`. Each rung is one `Rscript --vanilla analysis/r/glmm.R` call through
`rbridge.run_r` (`lme4::glmer`, binomial logit, `bobyqa`, `maxfun` 200,000). A rung is
accepted when it converged (optimizer code 0, no lme4 convergence message, no convergence
warning) and `isSingular(tol = 1e-4)` is false; the ladder stops there. Study A has no
slopes, so its other model rungs are logged `skipped`. With no stable rung the log ends
with `descriptive` and the report points to the participant/dyad aggregates. Every
attempt keeps its formula, flags, R messages verbatim and reason;
`estimates/glmm/<model>.json` follows `glmm-log.schema.json` and records the R and lme4
versions; `run_r` refuses an R whose versions differ from `analysis/r/pins.dcf`.

`--glmm`: `require` fits through R and refuses to continue without it; `skip` logs every
model as not fitted; `auto` (default) is `require` on REAL roots and, on SYNTHETIC roots,
fits when `Rscript` is found, else logs "R not available". Tests that fit real models are
marked `needs_r` and run in the CI `r` job.

## 7. Report (`estimates/`)

| File | Content |
| --- | --- |
| `report-<study>.md` | `av-data-kind` line, SYNTHETIC banner, key results, sections 1-8 in plan order (Study A: no section 4; Study B: no section 3), every table with its `TableMeta` line and CSV link, GLMM log links |
| `tables/<study>-<nn>-<id>.csv` | every table in full, first column `data_kind` |
| `tables/<study>-index.csv` | table, section, file, title, independent unit, units, planned units, trials, missing units |
| `glmm/<model>.json`, `-fixed.csv`, `-random.csv`, `-data.csv` | ladder log, accepted rung's estimates, model data |
| `runs/run-<study>-<set>.json` | inputs with SHA-256, seed labels, outputs with SHA-256 |
| `manifest.json` | every file of the area, union of the runs' inputs and seeds (`outputs-manifest.schema.json`) |

Tables by section (ids): 1 `flow-enrollment` (pre-allocation eligibility records,
eligible persons, screening cases (**Pending** while `screening_cases_n` is null), revealed
units and person slots, spares used, bank-unavailable records, from
`reconciled/enrollment.csv`; every count **Pending** when the root has no enrollment row),
`flow-persons`, `flow-units`, `flow-visits`, and a note that failed generation waits for
the generation audit tables (#24); 2
`fidelity-delivery` (faults by type, lost opportunities, valid delivery, uncertain
onsets, by condition), `fidelity-novelty`, plus the scoring check (logged exact score
versus recomputed Y), reconciliation states, discrepancies and the golden manifest; 3
`a-primary`, `a-batches`, `a-books`, `a-secondary`, `a-designer`; 4 `b-primary`,
`b-bootstrap`, `b-dyads`, `b-exploratory`; 5 `secondary-endpoints`,
`secondary-contrasts`, `secondary-rt`; 6 ratings and consultation: **Pending** (no
export format); 7 `sens-valid-delivery`, `sens-valid-denominators`, `sens-timing`,
`sens-glmm`, `sens-bounds`, `sens-tipping-summary`, `sens-tipping-grid` (CSV only); 8
`deviations-missing`, `deviations-lost`, `deviations-discrepancies` (when present) and
generated bounded conclusions.

**Denominators.** Every `TableMeta` line is computed from the data behind its table, and
contributing + missing = planned units always holds: person tables count the assigned
persons with data for that table (for example persons with opportunities on the battery
for `secondary-rt`, persons in the fitted models' data for `sens-glmm`, 0 when every model
ended `descriptive`); tables with several rows of different support (`sens-timing`,
`secondary-contrasts`, `a-designer`) state the best-supported row and say so in the note.

## 8. Synthetic data (`simulate`, `synthetic_tables`)

**Data-generating process** (ported from the planning simulation of analysis plan
section 7): each condition cell's logit intercept solves `E[expit(alpha + Z)] = target`
by 60-point Gauss-Hermite quadrature over the cell's total latent variance (residual
checked against independent integration in the tests), so effects are on the probability
scale. Study A latent terms: batch, book, person, semantic item (shared across batches),
book x item, person x item; Study B: dyad, person, item, dyad x item, person x item, dyad
role slope, dyad and person scaffold slopes (on centred codes). Both repetitions of a
message share every latent term. Added here: technical faults per opportunity
(`fault_rate`; a share are lost opportunities, the rest missing playback, audio underrun
or presentation freeze), primary-endpoint attrition (`attrition`; a share withdraws
during the battery), late Study B W1 visits (`late_rate`), missed later visits. All
missingness is MCAR, as in the planning simulation.

| Scenario | Study | Effect | Heterogeneity | Set |
| --- | --- | --- | --- | --- |
| `central-A`, `pessimistic-A` | A | A3-A2 10 pp | central, pessimistic | confirmatory (18 batches, 216 learners) |
| `null-A`, `null-pessimistic-A` | A | 0 | central, pessimistic | confirmatory |
| `pilot-A` | A | 10 pp | central | pilot (3 batches, 18 learners) |
| `central-B`, `pessimistic-B` | B | C 10, S 10 pp | central, pessimistic | confirmatory (64 dyads, 128 people) |
| `null-B`, `null-pessimistic-B` | B | C 0, S 0 | central, pessimistic | confirmatory |
| `null-co-B`, `null-scaffold-B` | B | C 0 / S 10; C 10 / S 0 | central | confirmatory |
| `pilot-B` | B | C 10, S 10 pp | central | pilot (8 dyads) |

Baseline accuracy .65; attrition .05 (A) and .15 (B) per person; faults .02 per
opportunity; late W1 .02. Variance components are the planning simulation's (central and
pessimistic latent SDs, stored as variances in `Scenario.variances`).

Dataset `k` of a scenario draws from `seeds.rng(seed, scenario, "dataset", k)`. The
**fast path** (`dataset_results`, `operating_characteristics`) aggregates the primary
draws directly and applies the same `one_sample_t` and `holm`; the **table path**
(`simulate_dataset`) turns the same draws into full `trials`/`endpoints` rows on the
schedule structure of the DEMO allocation (every schedule item of a held visit, about
40,000 trial rows per full-size study) plus the key and lists. A test runs the whole
pipeline on the table path and gets the fast path's primary estimate and p value.

`simulate` writes `estimates/simulation/<scenario>-operating-characteristics.csv`,
`<scenario>-datasets.csv` (one row per dataset and contrast), `<scenario>-scenario.json`
and the combined `operating-characteristics.csv` (`operating-characteristics-row.schema.json`:
scenario, study, contrast, rule, true effect, datasets, rejections, rate, plug-in MCSE,
Wilson 95% Monte Carlo interval, mean estimate, mean unit SD, mean units, unavailable
datasets, seed). Contrasts: A `A3-A2` (primary), `A3-A1`, `A2-A1` (Holm); B `C`, `S`
(Holm), `any` (family-wise under the global null), `both`. `--write-dataset` also
writes dataset 0 into the root (`derived/` tables merged with other studies' rows,
`derived/manifest.json`, key and lists through `paths.write_synthetic_input`).

## 9. Validation

| Check | Where |
| --- | --- |
| t, df, p and intervals against hand-computed closed forms within 1e-10; Holm fixtures (.02, .04) and (.03, .04) | `tests/analysis/test_pipeline_estimators.py` |
| fewer than 2 complete differences -> unavailable (estimator and report) | `test_pipeline_estimators.py`, `test_pipeline_run.py` |
| scoring rules, battery denominators, partial batteries | `test_pipeline_scoring.py` |
| bounds and tipping grid against hand calculation; Study B imputed whole = (structured + dictionary) / 2 in every cell, C and S from the same values | `test_pipeline_missingness.py` |
| end-to-end rules by hand on edited pilot roots: late W1 visit (primary versus timing population), partial primary battery in the bounds, 95% and 97.5% B intervals, Holm thresholds .025/.05, bootstrap strata, first pass, `not_run` refusal, enrollment flow, denominators; GLMM model-data coding (role, format, method, designer) and timing filter | `test_pipeline_rules.py`, `test_pipeline_glmm.py` |
| ladder order with singular fits (fake runner; real lme4 fixture, `needs_r`) | `test_pipeline_glmm.py` |
| null scenario: 2,000 synthetic A datasets reject between .040 and .060 | `test_pipeline_simulate.py` |
| full-size A (216 learners) and B (128 people) end to end under 30 min: `analysis/ci/34.sh` on every CI runner (fails at 30 min), the `needs_r` test with lme4, `AV_FULL_E2E=1` locally; report order, denominators, manifest, determinism, refusals (pilot size by default) | `test_pipeline_run.py`, `analysis/ci/34.sh` |
| CI evidence: synthetic reports, operating characteristics, timings | `analysis/ci/34.sh` (artifact `analysis-evidence-<os>`) |

## 10. Decisions and open items

* **Technical failures score 0 whatever was logged** (a verified failure "contributes
  0"); rows that are not valid deliveries but carry no fault (for example an uncertain
  onset without a code) keep their response in the operational score and leave the
  valid-delivery score.
* **Time to commit** censors abstentions and timeouts at the window; the competing
  "abstain" event is reported as a count beside the median.
* **Per-family denominators** are half of the scheduled opportunities for the assessment
  batteries (18 of 36 at W1/W4), as the matrix balances K and Q.
* **Tipping reference** is the observed mean of the condition among complete persons
  (Study B: of the role and family; 0.5 when none), the estimate the all-assigned point
  estimate; a zero estimate counts as a change of direction. Study B uses one set of
  imputed family scores per person and cell for both contrasts (plan section 6).
* **GLMM engine**: R `lme4::glmer` through `Rscript` (adopted in the skeleton); one R
  call per rung so the ladder decision stays in Python and is testable without R.
* **Supporting models** use in-window and anchor visits only; late visits stay in the
  timing sensitivity.
* **Pending**: ratings and consultation exports (no format); generation fallback flags
  for the non-fallback sensitivity (#24); screening cases (#73 with #33); a timing model
  with actual delay; real-data runs (out of scope).
