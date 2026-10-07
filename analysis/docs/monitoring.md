# Integrity monitoring dashboard (#35, O4.5.3)

The dashboard lets masked study staff monitor data collection: enrollment, allocation
progress, attrition, visit windows, apparatus faults, visit overruns, reconciliation
status, open deviations, and comfort and withdrawal reports. It never shows an outcome,
and it never splits anything by condition (analysis plan section 8). Staff can use it
without unmasking.

Contract for other components: [`docs/interfaces/analysis.md`](../../docs/interfaces/analysis.md),
section "Integrity dashboard (#35)". Shared rules: [`architecture.md`](architecture.md).

## 1. Use

```sh
# After each imported visit (reconcile --all, derive, dashboard):
uv run --project analysis av-analysis refresh --root <data root>
# The dashboard alone, from the reconciled tables already in the root:
uv run --project analysis av-analysis dashboard --root <data root>
```

Open `<data root>/monitoring/index.html` in any browser, for example from the operator
console. The page is one static file: inline CSS, no script, no network access, no
external fonts or images. A `Content-Security-Policy` element in the page blocks all of
them. The page follows the browser's light or dark colour scheme.

Exit codes of `dashboard`:

| Code | Meaning |
| --- | --- |
| 0 | Dashboard written. |
| 1 | Dashboard written, and at least one red alert (suspension event) is shown. |
| 2 | Input refused: no data root, a source table is missing or does not follow its specification, or a table has a disallowed column. Nothing is written. |

`refresh` continues after exit 1 and stops at exit 2.

## 2. Modules

| Module | Contents |
| --- | --- |
| `monitoring` | `PANELS`, `SOURCE_TABLES`, `ALLOWED_COLUMNS`, `EXCLUDED_COLUMNS`, `allowlist()`, `disallowed_columns()`, `load_monitoring_data()`, `check_monitoring_data()`, `dashboard_document()`, `render()`, `write_dashboard()`, `SCHEMAS`, the `dashboard` command |
| `monitoring_metrics` | `build_document()`: the panel metrics as one JSON document; `dashboard_data_schema()`; `TARGETS`; `TRIGGERS` |
| `monitoring_html` | `render_document()`: the static page, from the metrics document only |
| `monitoring_demo` | `demo_tables()`, `write_demo_root()`, `python -m av_analysis.monitoring_demo`: synthetic (DEMO) reconciled tables, with injected suspension events |

## 3. Inputs and outputs

The dashboard reads three reconciled tables of a data root, and nothing else:

- `reconciled/visit-status.csv`
- `reconciled/discrepancies.csv`
- `reconciled/enrollment.csv`

It never reads `raw/`, `inputs/`, `keys/`, `derived/` or `estimates/`. Reconciliation
(#33, `derive`) writes the three tables (`derived.TABLES`).

It writes three files to `monitoring/`, through `paths.write_output` (same data kind as
the root, watermarked):

| File | Content |
| --- | --- |
| `index.html` | The page. `<meta name="av-data-kind">`; for synthetic data a visible `SYNTHETIC` banner at the top and at the bottom. |
| `dashboard.json` | The panel metrics (`analysis/schema/dashboard-data.schema.json`). Pilot reviews (O6.3.4, O6.3.5) read the fault and overrun rates here. |
| `manifest.json` | SHA-256 and size of the two files and of the three input tables (`outputs-manifest.schema.json`). |

**Last update.** No output contains a wall-clock time (architecture section 6). The page
shows "Data as of", the later of the latest visit date and the latest reveal-log date, and
the SHA-256 of each input table. The same inputs give the same bytes on every platform.

## 4. Masking: three gates

1. **Header check, before any row is read.** For each table, every column must be in
   `ALLOWED_COLUMNS` or in `EXCLUDED_COLUMNS` (reviewed columns that the loader drops).
   The build fails (`DisallowedColumnError`, exit 2) for:
   - a column that `masking.forbidden_reason(column, "masked")` names (outcome,
     response, response time, hidden answer, rating, condition, personal or staff);
   - a free-text template column;
   - any other column.

   The check covers all three tables before any output is written. Thus a table that
   holds `exact_correct`, `response_action`, `response_time_ms`, `method_masked`, `role`
   or `scaffold_family` stops the build, and none of these fields reaches the page.
   This is fail-closed: if #33 adds a column to a source table, the dashboard refuses
   the table until #35 adds the column to one of the two lists.
2. **Row check.** `check_monitoring_data` refuses any row key that is not allow-listed,
   any other table, and rows of the other data kind. It also covers data built in
   memory.
3. **Document check.** The metrics document is validated against its schema before it
   is rendered. The schema has `additionalProperties: false` at every level, and every
   string is a pattern, an enumeration or generated text from package constants. The
   document also passes `masking.forbidden_keys(doc, "masked")`. The HTML renderer
   receives only this document, never a table row.

### Allow-listed columns

| Table | Columns read |
| --- | --- |
| `visit-status` | `data_kind`, `study`, `set`, `unit_id`, `person_id`, `visit`, `visit_id`, `station_id`, `visit_state`, `reconciliation`, `checks_failed`, `discrepancies_n`, `unresolved_n`, `suspension_events`, `visit_date`, `days_since_anchor`, `window_lo_days`, `window_hi_days`, `timing`, `pair_gap_hours`, `pair_gap_ok`, `overrun`, `opportunities_n`, `fault_n`, `fault_<type>_n` (7 types), `comfort_flag`, `deviations_n`, `open_deviations_n`, `comfort_deviations_n`, `withdrawal_deviations_n` |
| `discrepancies` | `data_kind`, `study`, `visit_id`, `seq`, `check`, `code`, `resolved`, `suspension_event` |
| `enrollment` | `data_kind`, `study`, `set`, `planned_units_n`, `planned_persons_n`, `eligibility_records_n`, `eligible_persons_n`, `screening_cases_n`, `revealed_units_n`, `revealed_persons_n`, `spares_used_n`, `bank_unavailable_n`, `last_event_date` |

### Reviewed exclusions (dropped at load)

| Table | Column | Why it is not read |
| --- | --- | --- |
| `visit-status` | `booked_minutes`, `actual_minutes` | Durations are not shown per visit. In Study B, the active session and the yoked session have different content, so a duration could hint at a member's condition. Only the `overrun` flag is used, in totals. |
| `visit-status` | `visit_seq`, `anchor_visit`, `report_sha256` | Ordering only, implied by `windows.WINDOWS`, or audit detail. |
| `discrepancies` | `detail` | Generated text. The page shows code titles and first steps from `codes.code()`, so no text from the tables reaches the page. |
| `discrepancies` | `rows`, `deviation_id`, `unit_id`, `person_id`, `visit`, `visit_seq` | Audit detail, or implied by `visit_id`. |
| `enrollment` | `reveal_log_sha256` | Audit detail; the page shows the input table hashes. |

### Other masking decisions (for the advisor review)

- **No split by condition.** Faults and overruns are shown in total, per study and set,
  per station and per visit type. They are never shown per person, per book, per
  method, per member role or per scaffold. Book IDs are not in the source tables.
- **Study B per dyad, never per member.** The active session comes before the yoked
  session. Thus member-level order, dates or durations could show which member is
  active. For this reason:
  - allocation progress counts members per dyad and visit ("1 / 2 held"), and does not
    say which member;
  - pair timing is listed once per dyad and visit;
  - visit dates are reduced to one value, the latest date.
- **Coded IDs only.** The page shows only person-slot IDs (`A-C07-L03`, `B-C12-M1`),
  visit IDs, unit IDs and station IDs. It shows no names, contact data, coded
  participant IDs or staff IDs (the staff columns are forbidden by `masking`). A test
  makes sure that every string in the document matches an ID pattern, an enumeration
  or generated text, and that the page contains no e-mail address or phone number.
- **Visit IDs are listed only where staff must act:** red alerts, missed visits,
  withdrawals, window exceptions, failed reconciliations, open deviations, and comfort
  and withdrawal reports. Faults and overruns are not listed per visit.

**Advisor sign-off on masking: Pending (human).** The advisor reviews this section, the
allowlist in `monitoring.py` and a synthetic dashboard (CI artifact
`analysis-evidence-ubuntu-latest`, `35/`), then records approval as a comment on issue #35.

## 5. Metrics

Every number is a count, or a sum of cells, of the three tables. Thus the page equals
the reconciliation totals. The tests compute every value straight from the CSV text and
compare it with the page and with `dashboard.json`:

- each `data-metric` element (a count);
- each `data-list` table or list: missed visits, withdrawals, window exceptions, dyad
  pairs outside 24 h, failed reconciliations, open deviations, comfort and withdrawal
  reports, red-alert visit IDs, and the fault and overrun rates with their trigger
  status ("above trigger" in red);
- each amber trigger (`<li data-trigger>`), against the protocol thresholds.

| Panel | Metric | Definition |
| --- | --- | --- |
| Enrollment | revealed persons and units, remaining, status | `enrollment` row of the study and set. Target from `monitoring_metrics.TARGETS` (Study A: batches × 3 books × learners per book, 216 confirmatory and 18 pilot; Study B: dyads × 2, 128 and 16). Status "target reached: stop" when revealed persons ≥ target. |
| Enrollment | planned units and persons, eligibility records, eligible persons, screening cases, spares used, bank unavailable, last reveal | As in `enrollment`. Screening cases show "Pending" while their source is not agreed (#73 with #33). |
| Allocation | slots revealed per unit; held / expected per visit; missed, withdrawn, pending | `visit-status` rows of the unit (batch, dyad or spare slot). |
| Attrition | persons, withdrawn persons, visits by state, missed visit IDs, withdrawals | Withdrawn person: any visit in state `withdrawn`; listed with the first withdrawn visit. |
| Windows | held, in window, early, late, undated per visit; exceptions; pairs outside 24 h | `timing` of held visits (`windows.classify` by #33). Undated = `unknown`. Pairs: Study B held V1-V3 rows with `pair_gap_ok` set, grouped by dyad and visit. |
| Faults | held visits, opportunities, faulted, rate, by type | Rate = Σ `fault_n` / Σ `opportunities_n` (accounted opportunities, lost ones included). Trigger: rate > 5% (`vocab.FAULT_RATE_TRIGGER`). By type: Σ `fault_<type>_n` (an opportunity can have more than one type). In total, per study and set, per station. |
| Overruns | visits checked, overran, share | Checked = `overrun` not empty; share = overran / checked; trigger: share > 10% (`vocab.OVERRUN_SHARE_TRIGGER`; an overrun is more than 10 min over the booking, set by #33). In total, per study and set, per visit type, per station. |
| Reconciliation | expected, held, passed, failed, not run, held but not reconciled | `reconciliation` and `visit_state` of `visit-status`. Failed visits listed with checks failed, discrepancies and unlinked ones. |
| Reconciliation | discrepancies by code | `discrepancies` rows per code: count, not linked (`resolved` false), title and first step (`codes.code()`). |
| Reconciliation | deviations, open, comfort, withdrawal, comfort flags | Sums of the `visit-status` counts; visits with open records and with comfort or withdrawal reports are listed. |

**Red alerts** (suspension events, `codes.SUSPENSION_EVENTS`): wrong-file mapping, leaked
answer display, changed old waveform. A discrepancy raises its event when its
`suspension_event` column is set, or when its code maps to an event (defence in depth). A
`visit-status` row that lists the event also raises it. One red box per event shows the
codes, the number of discrepancies and how many are not linked to a deviation record,
and the affected visit IDs. A linked deviation record does not remove the alert: the
record documents the amendment, and the event stays on the page.

**Amber triggers** (`monitoring_metrics.TRIGGERS`): enrollment target reached or
exceeded, enrollment and visit-status counts that differ, fault rate above 5%, overrun
share above 10%, failed reconciliations, held visits without a reconciliation result,
and dyad sessions outside the 24 h pair rule. "Above" is strict, as in the analysis
plan: exactly 5% of opportunities faulted, or exactly 10% of visits overrunning, does
not raise the trigger. The enrollment check works in both directions: an `enrollment`
row whose revealed persons differ from the persons in `visit-status`, and a study and
set with `visit-status` rows but no `enrollment` row. The enrollment panel also shows
an amber line for such a study and set, so it does not disappear from the panel.

## 6. Reading guide

Read the panels from top to bottom. Each panel starts with a one-line hint.

1. **Header.** Check "Data as of" and the source-table hashes. If the date is older
   than the last visit you imported, run `av-analysis refresh --root DIR` again. A purple
   `SYNTHETIC` banner means demonstration data, not study data.
2. **Suspension alerts.** A red box means: stop collection on the affected visits (or
   station) now, keep every record, and write the amendment. Give the visit IDs to the
   reconciliation lead. The box stays on the page: a linked deviation record documents
   the amendment but does not remove the event. A green box means no suspension event. Then read the amber triggers: each
   one names the study and set and the numbers behind it.
3. **Enrollment against frozen targets.** The first table compares revealed person
   slots with the frozen target. At "target reached: stop", assign nobody more. Do not
   refill a withdrawal after assignment. The second table shows the list sizes, the
   eligibility records, the screening cases ("Pending" until their source exists), the
   Study B spare slots and bank-unavailable records, and the last reveal date. An amber
   line "no row in reconciled/enrollment.csv" means that visit-status has visits for a
   study and set that the enrollment table does not have: check the reveal log and run
   the reconciliation again.
4. **Allocation progress per batch or dyad.** One row per batch (Study A) or dyad slot
   (Study B). "Slots revealed" shows how many of the unit's person slots are assigned.
   Each visit column shows held of expected visits. For a dyad, "1 / 2" means one of the
   two members held the visit. It does not say which member.
5. **Attrition and missed visits.** Visits by state for each visit type, then the
   missed visit IDs and the withdrawals (person slot and first visit not held).
6. **Visit-window adherence.** For each visit after the anchor, the number of held
   visits in, before and after the window, and without a date. The exceptions table
   lists each early, late or undated visit with its day and window. These visits need a
   window deviation record. In Study B, the pair line counts dyad sessions outside the
   pair rule (the yoked session starts after the active one ended and within 24 h of its
   start; `windows.yoked_gap_ok`).
7. **Faults by type and station; visit overruns.** Rates in red are above the trigger:
   more than 5% of accounted opportunities with an apparatus fault, or more than 10% of
   visits more than 10 min over the booking. A station above the trigger needs an
   apparatus check. The type table shows which kind of fault occurs.
8. **Reconciliation, open deviations, comfort and welfare.** Failed or not-yet-reconciled
   visits need the reconciliation lead. The code table gives the first step for each
   discrepancy code. Visits with open deviation records need a resolution. Comfort and
   withdrawal reports are listed per visit for the welfare review.

## 7. Synthetic demonstration data

`monitoring_demo` writes synthetic reconciled tables into a new SYNTHETIC root (a
`DEMO-` seed is required). It is a stand-in for #33 `synth-logs` and `derive`, which
are built in parallel. The tables go through `derived.table_bytes` and follow the same
contract.

- Default size is full: every planned unit and person of the chosen sets, with every
  visit.
- `--progress` reveals a share of the units and leaves the last tenth of them in
  progress.
- Rows hold visit states, dates and windows, faults (station `ST03` is faulty on
  purpose), overruns, comfort flags, deviation counts and discrepancies (window,
  yoked-gap and C8). They hold no outcomes.
- `--inject wrong_hash|changed_old_atom|answer_leak[=VISIT_ID]` adds the mapped
  suspension discrepancy. When there is no visit ID, a default held visit is used.

```sh
uv run --project analysis python -m av_analysis.monitoring_demo --out <dir> \
  --seed DEMO-local --set both --progress 0.8 \
  --inject wrong_hash --inject changed_old_atom --inject answer_leak
uv run --project analysis av-analysis dashboard --root <dir>   # exit 1: red alerts
```

After the restack on #33, `tests/analysis/test_monitoring_counts.py` also runs the real
chain: `synth-logs`, then `refresh`, with and without the `wrong_hash` and
`changed_old_atom` faults. Until then, these tests are skipped.

## 8. Evidence and timing

- Tests: `tests/analysis/test_monitoring.py` (allowlist, masking gates, output schema),
  `test_monitoring_counts.py` (page and `dashboard.json` = tables: counts, lists, rate
  status and triggers; property tests), `test_monitoring_alerts.py` (red alerts,
  triggers and their thresholds) and `test_monitoring_cli.py` (command, refresh,
  watermark, determinism, last update, timing).
- CI hook `analysis/ci/35.sh` builds a full-size synthetic dashboard with the three
  injected events. It times the regeneration, also tries the #33 chain, and on Linux
  takes a headless-Chrome screenshot. Everything goes to the artifact
  `analysis-evidence-<os>` (`35/`); no screenshot is committed.
- Regeneration on full-size synthetic data (both studies, pilot and confirmatory sets,
  1,188 expected visits) takes well under one second on a developer machine. The
  acceptance limit is 60 s (Proposed). The `refresh` timing with real reconciliation
  depends on #33.

## 9. Pending

- Advisor sign-off on masking (human): see section 4.
- Frozen targets: the planning budgets serve as targets until the sample-size
  decisions (O6.4.1, O6.4.3) freeze them; then update `TARGETS`.
- Screening-case source (#73 with #33): until then the page shows "Pending".
- Real reconciled tables from #33 (restack); then the chain tests run.
