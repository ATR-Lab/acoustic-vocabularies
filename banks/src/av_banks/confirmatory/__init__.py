"""Confirmatory Study B bank runs (#28, WBS O8.1.1): 64 dyad banks plus 8 spares.

The confirmatory banks are runs of the #26 builder (`av_banks.builder`, `av_banks.run`).
This subpackage adds the campaign around them, in the order of the issue checklist:

1. `plan`: check the G4 freeze manifest (#25's freeze guard, `freeze_check`) and its tag
   against the generation config (`genconfig.check_run_config`), create the 72 bank IDs
   (`bank-C001`..`bank-C064` main, `bank-C065`..`bank-C072` spares), bind each to its
   unit permutation and seed namespace, check the seeds (`seed_check`: unique across the
   72 banks, disjoint from the pilot) and record everything in `plan.json` (restricted
   storage).
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
