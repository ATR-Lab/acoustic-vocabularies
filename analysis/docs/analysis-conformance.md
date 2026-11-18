# Analysis conformance: rules, code and tests (#310, O4.5.5)

Each prespecified rule of the locked-data analysis is pinned by at least one test in
[`tests/analysis/test_analysis_conformance.py`](../../tests/analysis/test_analysis_conformance.py)
(`test_r<N>_...`). The rules below are short paraphrases for this repository; the
pipeline itself is described in [`pipeline.md`](pipeline.md). All tests run on SYNTHETIC
data only: pilot-size roots from the table path of `simulate`, edited so that each rule
matters, or unit-level synthetic values. Expected values are recomputed by hand from the
derived rows (exact-message Y, book means, unit differences, closed-form t tests,
interval ends enumerated one by one), not with the estimator under test.

```sh
uv run --project analysis pytest --import-mode=importlib -p no:cacheprovider \
    tests/analysis/test_analysis_conformance.py -m "not needs_r"
```

## Fixtures

| Fixture | Data | What was edited |
| --- | --- | --- |
| `plain_a` | `pilot-A`, seed `DEMO-conformance` | nothing |
| `study_a` | `pilot-A`, same seed | batch 1: no A2 learner has a D0 endpoint (missed visits), one A3 battery stopped after 12 trials and not resumed; batch 2: one A2 learner withdraws after 20 trials, one A3 learner's committed trial is replayed with the opposite outcome, the other A3 learner's D7 visit is held after its window; batch 3: one A1 learner has no derived row at all, the other missed every visit, one A3 learner has a verified fault on a correctly answered trial |
| `study_b` | `pilot-B`, same seed | in the first two complete pairs: the active member withdraws after 20 W1 trials; the yoked member's W1 visit is held after its window |

## Rule map

Status: **implemented** (the code already followed the rule; the test pins it), **fixed
here** (the test found a gap, fixed in this change), **not implemented**.

| Rule (paraphrase) | Status | Code | Tests |
| --- | --- | --- | --- |
| R1. Each primary contrast is a two-sided one-sample t test at alpha .05 of independent unit differences: Study A per batch A3 minus A2; Study B per pair active minus yoked (C) and the members' mean of structured minus dictionary (S) | implemented | `estimators.one_sample_t`, `a_book_means`, `a_batch_differences`, `b_dyad_differences`; `pipeline.a_primary_section`, `b_primary_section`, `a_decision` | `test_r1_study_a_primary_is_a_two_sided_t_test_of_batch_differences`, `test_r1_study_b_primary_contrasts_are_choice_and_teaching_dyad_differences` |
| R2. Study B's two primaries form one Holm family (smaller p at .025, then the larger at .05); Study A's two comparisons with A1 are secondary and Holm-adjusted together | fixed here | `estimators.holm` (`family_size`), `pipeline.b_family`, `a_primary_section` | `test_r2_study_b_two_test_holm_family`, `test_r2_study_a_secondary_holm_keeps_both_comparisons_in_the_family` |
| R3. A bootstrap of whole batches (A) or pairs (B) is a sensitivity analysis, with stored seed labels that reproduce it | implemented | `estimators.stratified_bootstrap`, `pipeline.BOOTSTRAP_SEEDS`, `report.write_files` (run record `seeds`) | `test_r3_bootstrap_seeds_are_stored_and_reproduce`, `test_r3_study_a_bootstrap_resamples_whole_batches` |
| R4. Sensitivity: available-observation mixed models that keep participant, vocabulary and item dependence; the observed trials of incomplete batteries enter them; without R the models are logged as not fitted | implemented | `glmm.model_specs`, `glmm.model_data`, `glmm.fit_ladder`, `pipeline.run_glmms` | `test_r4_models_keep_participant_vocabulary_and_item_dependence`, `test_r4_r5_observed_trials_of_incomplete_batteries_enter_the_models`, `test_r4_without_r_the_models_are_logged_as_not_fitted`, `test_r4_lme4_fits_the_available_observation_model` (`needs_r`) |
| R5. A missed visit, a visit outside its window or a withdrawal before the 36 primary trials leaves the endpoint missing, never 0; observed trials still enter the supporting models | implemented | `scoring.battery_scores` (score only when complete), `pipeline.endpoint_scores` (`planned_endpoint` timing), `glmm.model_data` | `test_r5_missing_endpoints_are_missing_never_zero`, `test_r5_out_of_window_primary_visit_leaves_the_primary_population`, `test_r4_r5_observed_trials_of_incomplete_batteries_enter_the_models` |
| R6. A verified technical fault on a played trial scores 0 when the participant continues; a valid-delivery analysis is reported beside the operational one | implemented | `scoring.score_trial`, `scoring.is_technical_failure`, `pipeline.sensitivities_section` (`sens-valid-delivery`) | `test_r6_verified_fault_scores_zero_and_valid_delivery_is_reported` |
| R7. After an interruption only unplayed trials may resume (Study A immediate test in the same session, delayed visits within their window); otherwise the endpoint is missing; scores of committed trials stand | implemented (analysis side) | `scoring.is_opportunity` (a replay is never a new opportunity), `scoring.battery_scores` (known part of a stopped battery), `pipeline.endpoint_scores` (window); upstream: one session per visit (reconciliation C1), retry rules (C4) | `test_r7_a_stopped_battery_that_was_not_resumed_is_missing_and_keeps_its_trials`, `test_r7_a_replayed_committed_trial_never_replaces_its_score`, `test_r7_a_delayed_visit_held_outside_its_window_is_not_an_endpoint` |
| R8. Allocated participants are never replaced: every one counts in the flow and in the bounds | implemented | `unmask.load_conditions` (`planned`), `pipeline.flow_section`, `missingness.all_assigned_bounds` | `test_r8_every_allocated_person_is_counted` |
| R9. Study A averages the available learner scores of a vocabulary with equal weight; a batch enters the primary test only when both its A3 and its A2 vocabulary have a learner endpoint | implemented | `estimators.a_book_means`, `a_batch_differences` | `test_r9_book_means_weight_learners_equally_and_batches_need_both_books` |
| R10. Study B: both primary contrasts need complete, in-window W1 batteries from both members | implemented | `estimators.DyadValues.complete`, `pipeline._b_primary` | `test_r10_both_contrasts_use_only_dyads_with_two_complete_in_window_members` |
| R11. Complete-unit estimates are labelled as assuming that missingness is unrelated to the outcomes | fixed here | `pipeline.COMPLETE_UNIT_NOTE` (primary, secondary and decision tables, section text, conclusions) | `test_r11_complete_unit_estimates_state_their_assumption` |
| R12. Bounds: over every allocated participant, each missing endpoint at 0 or 1 in the direction that lowers, then raises, each primary contrast (known parts of partial batteries kept) | implemented | `missingness.all_assigned_bounds`, `person_interval` | `test_r12_study_a_bounds_are_the_extremes_over_all_allocated_learners`, `test_r12_study_b_bounds_are_the_extremes_over_all_allocated_members` |
| R13. Tipping-point displays move the missing scores separately by condition | implemented | `missingness.tipping_grid`, `tipping_imputations` | `test_r13_study_a_tipping_moves_missing_a3_down_and_missing_a2_up`, `test_r13_study_b_tipping_moves_missing_scores_by_role_and_by_teaching_format` |
| R14. Practical criterion of 10 points: Study A judged with its 95% interval, Study B with two simultaneous 97.5% intervals; a Holm-significant contrast whose 97.5% interval includes 0 is said so; an upper limit below the criterion weighs against a benefit; an interval across the criterion leaves the practical value open | fixed here | `estimators.practical_reading`; `pipeline.Decision`, `decision_table`, `b_decisions` (`a-decision`, `b-decision`, key results) | `test_r14_practical_reading_of_an_interval`, `test_r14_decision_tables_judge_a_with_95_and_b_with_97_5_intervals`, `test_r14_holm_significant_contrast_with_97_5_interval_over_zero_is_flagged` |
| R15. A contrast that could not get its units before the freeze is an estimation contrast (estimate and interval with their precision, no test); if the choice contrast C is one, the teaching contrast S is tested alone at .05 without Holm | fixed here | `av-analysis run --estimation-contrast` (`pipeline.add_arguments`, `check_estimation`, `b_family`, `a_decision`), run record `estimation_contrasts` | `test_r15_choice_as_estimation_contrast_tests_teaching_alone`, `test_r15_tested_alone_differs_from_the_holm_family`, `test_r15_study_a_estimation_contrast_and_refusals`, `test_r15_run_option_is_recorded` |
| R16. Primary endpoint: exact-message accuracy over the 36 primary trials (action and target both right; wrong answers, "don't know" and timeouts score 0); accuracy on first presentations as a sensitivity | implemented | `scoring.score_trial`, `battery_scores`; `pipeline._secondary_specs` ("first pass only") | `test_r16_exact_message_needs_both_action_and_target`, `test_r16_primary_score_is_over_the_36_trials_and_first_pass_is_reported` |
| R17. Study A results by hand designer | implemented | `pipeline.a_primary_section` (`a-designer`), `glmm.model_specs` (`A-designer`) | `test_r17_study_a_reports_results_by_hand_designer` |

## What changed to conform (#310)

* **R2**: `holm(..., family_size=)` keeps the prespecified family size. Before, when one
  Study A secondary was unavailable (fewer than 2 batches), the other was tested alone at
  .05; now it is the first test of a two-test family, at .025.
* **R11**: the primary, secondary and decision tables, the primary sections and the
  bounded conclusions say that complete-unit estimates assume missing endpoints unrelated
  to the outcomes.
* **R14**: new decision tables `a-decision` and `b-decision` (after the primary table):
  the test decision, the judgement interval (A: 95%; B: 97.5%), its half-width, the
  criterion and the reading (`against benefit`, `unresolved`, `meets criterion`), and the
  flag for a Holm-significant contrast whose 97.5% interval includes 0; one key-results
  line per contrast.
* **R15**: `av-analysis run --estimation-contrast A3-A2|C|S` (repeatable). The contrast
  keeps its estimate and intervals (95%, and 97.5% for Study B) with their half-width, and
  loses its t, p and Holm columns; with one Study B contrast left, it is tested alone at
  .05; a contrast of the other study is refused (exit 2). The run record lists the
  contrasts (`estimation_contrasts`) and the command line.

## Decisions to confirm

* Out-of-window visits (R5): their trials stay out of the available-observation models
  and enter the timing sensitivity (`glmm.MODEL_TIMINGS`), as decided in `pipeline.md`
  section 10. If the rule means that these trials enter the models too, the change is the
  timing filter of `glmm.model_data`.
* Practical reading at the boundary (R14): an upper limit exactly at 10 points counts as
  `unresolved`, a lower limit exactly at 10 points as `meets criterion`.
* Judgement intervals with an estimation contrast (R14, R15): Study B keeps the 97.5%
  intervals for both contrasts even when only one is tested.
* Estimation contrasts in the sensitivity tables (R15): the timing, valid-delivery and
  secondary tables still print a p value for them, labelled as sensitivity or secondary.
* First presentation (R16) is reported in section 5 (`secondary-contrasts`, "first pass
  only"), not in section 7.

## Open items

* Resumption across sessions (R7): a visit is one export with one session
  (`raw/<visit_id>/`, reconciliation C1). A delayed visit resumed on another day within
  its window, in a second session, is not represented in the derived tables yet; that
  needs a decision for #33 (reconciliation and `derive`). The pipeline's part (missing
  endpoint, committed scores kept, window) is pinned.
* Sample-size presets: the fast path (`simulate.draw_a`, `draw_b`) takes any size and is
  tested at 30 batches and at 84 and 88 pairs
  (`test_presets_study_a_fast_path_at_30_batches`,
  `test_presets_study_b_fast_path_at_84_and_88_dyads`). The table path
  (`simulate_dataset`, `synthetic_tables`) builds the allocation by set name and needs the
  presets of #298 to build other sizes.
* `docs/interfaces/analysis.md` does not list the decision tables or
  `--estimation-contrast` yet (that file belongs to another open change).
* The `needs_r` test runs only where `Rscript` with the pinned packages is installed (CI
  job `r`).
