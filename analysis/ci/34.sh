#!/usr/bin/env bash
# Issue #34 evidence (analysis/ci/README.md): synthetic section 9 reports of full-size
# Study A (216 learners) and Study B (128 people) with their end-to-end timings, and the
# operating characteristics over 2,000 synthetic datasets per scenario. SYNTHETIC only.
# Supporting GLMMs run in the 'r' job (needs_r tests); here R is absent, so the GLMM logs
# say "not fitted".
set -euo pipefail

out="${AV_CI_OUT:?run from the analysis workflow (AV_CI_OUT)}"
av() { uv run --project analysis --locked av-analysis "$@"; }

# Operating characteristics (seed fixed in tests/analysis/test_pipeline_simulate.py).
scenarios="null-A,null-pessimistic-A,central-A,pessimistic-A"
scenarios="$scenarios,null-B,null-pessimistic-B,null-co-B,null-scaffold-B,central-B,pessimistic-B"
av simulate --scenario "$scenarios" --datasets 2000 --seed DEMO-o4.5.2-null --out "$out/oc-root"
cp "$out/oc-root/estimates/simulation/operating-characteristics.csv" "$out/operating-characteristics.csv"

# Full-size end-to-end runs: synthetic dataset, then every section 9 output.
root="$out/e2e-root"
: > "$out/timings.txt"
for study in A B; do
  start=$(date +%s)
  av simulate --scenario "central-$study" --datasets 1 --seed DEMO-o4.5.2-e2e --out "$root" --write-dataset
  av run --study "$study" --data "$root" --glmm auto
  end=$(date +%s)
  echo "Study $study (${RUNNER_OS:-local}): simulate + run in $((end - start)) s" | tee -a "$out/timings.txt"
  cp "$root/estimates/report-$study.md" "$out/report-$study.md"
  if [ $((end - start)) -ge 1800 ]; then
    echo "Study $study end-to-end took 30 min or more" >&2
    exit 1
  fi
done

# Fail when the null rejection rate leaves the acceptance range.
uv run --project analysis --locked python - "$out/operating-characteristics.csv" <<'PY'
import csv, sys
with open(sys.argv[1], newline="", encoding="utf-8") as f:
    rows = {(r["scenario"], r["contrast"]): r for r in csv.DictReader(f)}
rate = float(rows[("null-A", "A3-A2")]["rate"])
print(f"null-A A3-A2 rejection rate over 2000 datasets: {rate:.4f}")
sys.exit(0 if 0.040 <= rate <= 0.060 else 1)
PY
