"""Unattended bank runs with checkpoint and resume (#267, WBS O4.3.3).

`python -m av_banks.unattended run JOB.json` runs a job file of stages in order (pilot
banks #27, then a confirmatory campaign #28 or its DEMO rehearsal) by starting the
existing command lines (`python -m av_banks.pilot ...`, `python -m av_banks.confirmatory
...`) as child processes. It keeps `state.json` in the job root (atomic writes), so a
restart after a kill, a reboot or a model-server outage resumes where it stopped. It
waits for the model server before every step that calls the model, and it applies only
the existing crash rules, logged: crashed confirmatory banks are rebuilt (new version,
new seed namespace, reason recorded) up to a per-bank cap, and a crashed pilot run is
archived as the record and replaced by a run in a new root with `--major k+1`. Anything
else stops for a person with the state kept.

Modules: `job` (job file, data policy), `state` (checkpoint, journal), `runtime` (job
lock, children, model probe), `session` (one supervisor session), `stages` (the stage
drivers), `supervisor` (`run`, `status`, `check`), `demo` (DEMO inputs and job),
`rehearsal` (the full-size DEMO rehearsal of the confirmatory campaign: its job, its
guards and its summary), `cli`. Runbook: `banks/docs/unattended-run.md`.
"""
