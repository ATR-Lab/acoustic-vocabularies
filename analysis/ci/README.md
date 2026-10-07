# CI evidence hooks

The `test` job of `.github/workflows/analysis.yml` runs every `analysis/ci/*.sh` script
in name order with bash, on Ubuntu, macOS and Windows, after the tests pass. Each issue
adds only its own script, so no issue edits the workflow:

| Script | Issue | Evidence |
| --- | --- | --- |
| `33.sh` | #33 | synthetic roots for every visit type, sample reconciliation reports, fault-injection suite |
| `34.sh` | #34 | synthetic section 9 report, null-scenario operating characteristics |
| `35.sh` | #35 | synthetic dashboard and its screenshot |

Rules:

- Run from the repository root; call the package with `uv run --project analysis --locked`.
- Write outputs only below `$AV_CI_OUT` (`analysis/out/ci/<script name>/`, created before
  the script runs, git-ignored). The workflow uploads `analysis/out/ci/` as the artifact
  `analysis-evidence-<os>`, also when a step fails.
- Synthetic `DEMO-` data only; outputs carry the `SYNTHETIC` watermark.
- A script that needs one runner (for example a browser screenshot) checks `RUNNER_OS`
  itself and exits 0 elsewhere.
- Exit non-zero to fail the job.
