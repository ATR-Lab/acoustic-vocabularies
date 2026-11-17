"""Confirmatory Study B bank runs (#28, WBS O8.1.1): one bank per dyad slot and spare.

The confirmatory banks are runs of the #26 builder (`av_banks.builder`, `av_banks.run`).
This subpackage adds the campaign around them, in the order of the issue checklist:

1. `plan`: check the G4 freeze manifest (#25's freeze guard, `freeze_check`) and its tag
   against the generation config (`genconfig.check_run_config`), take the campaign size
   from the unit set (`size`: `B-C01`..`B-C<n>` main dyad slots, `B-S01`..`B-S<m>`
   spares; optionally cross-checked against a sample-size preset, #266), create the bank
   IDs (`bank-C001`..`bank-C<n>` main, then the spares; 64 + 8 = 72 with the default
   preset), bind each to its unit permutation and seed namespace, check the seeds
   (`seed_check`: unique across the banks, disjoint from the pilot) and record everything
   in `plan.json` (restricted storage).
2. `runner`: build the banks in parallel (one #26 run per bank) with progress
   monitoring, a halt on model-server failures before they can fail a cell, and crash
   detection.
3. `register`: `banks verify` on every bank, the run timing log, the register
   (`register.csv`, `register.json`), the deterministic archive and its SHA-256, the
   complete and unavailable counts with the escalation rule, the G5B report, and the
   register-commit timestamp check.
4. `rehearsal`: the whole procedure on `DEMO-` IDs with a scripted proposer (no model).

Command line: `python -m av_banks.confirmatory --help` (`cli`). Design and decisions:
`banks/docs/confirmatory-banks.md`.
"""
