# DEMO reconciliation reports (synthetic)

Sample outputs of the reconciliation (#33), generated from the public seed
`DEMO-o4.5.1-example` by

```sh
uv run --project analysis av-analysis synth-logs --demo-seed DEMO-o4.5.1-example \
  --out analysis/examples/reconciliation-demo --examples
```

They are synthetic format examples (`data_kind` `SYNTHETIC`): no participant, no study
material, no real package. `tests/analysis/test_reconcile_synthetic.py` and
`test_reconcile_faults.py` regenerate them and fail if any byte differs.

Clean reports, one per visit type (every check passes, 0 discrepancies):

- `A-D0-clean.json`, `A-D7-clean.json`: Study A learner, day 0 and day 7.
- `B-V1-clean.json`, `B-V2-clean.json`, `B-V3-clean.json`: Study B acquisition visits
  (C5 store growth and C6 yoked ledger apply).
- `B-W1-clean.json`, `B-W4-clean.json`: Study B follow-ups.

Fault examples:

- `A-D0-wrong_hash-undocumented.json`: a played waveform that matches neither expected
  hash, without a deviation record: C3 `WAVEFORM_HASH_MISMATCH` (suspension event
  `WRONG_FILE_MAPPING`) and C8 `DEVIATION_MISSING`; the visit fails.
- `B-W1-missing_trial-documented.json`: a trained trial lost to a logger failure that a
  deviation record verifies: C2 `COUNT_MISSING_TRIAL` is resolved (`explained`) and the
  visit passes; `derive` turns it into a `row_source` deviation trial row.

`fault-suite.csv`: the fault-injection suite (every fault of `codes.FAULT_INJECTIONS` on
every visit type it applies to, undocumented and documented; 95 cases). Columns: `fault`,
`visit_type`, `visit_id`, `documented`, `expected_code`, `detected`, `resolved`,
`deviation_missing`, `status` (of the visit's report), `ok`, `codes` (every code
reported, `|`-joined). Rules: [`analysis/docs/reconciliation.md`](../../docs/reconciliation.md).
