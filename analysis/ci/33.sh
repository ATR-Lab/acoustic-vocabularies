#!/usr/bin/env bash
# CI evidence for #33 (reconciliation); rules in analysis/ci/README.md.
#  1. A SYNTHETIC root with clean logs for every visit type (A D0, D7; B V1-W4), then the
#     operator command `refresh` on it: every visit must pass. Raw-file SHA-256 values are
#     listed before and after the run and must be identical.
#  2. Reconciliation time of every visit (fails above the proposed 30 s).
#  3. The fault-injection suite: every fault on every visit type it applies to,
#     undocumented and documented (fault-suite.csv and one report per case).
# Outputs go to $AV_CI_OUT (uploaded as analysis-evidence-<os>). Synthetic DEMO data only.
set -euo pipefail

out="${AV_CI_OUT:?AV_CI_OUT is not set}"
seed="DEMO-o4.5.1-ci"
root="$out/root"

av() { uv run --project analysis --locked av-analysis "$@"; }
py() { uv run --project analysis --locked python "$@"; }

raw_hashes() {
  py - "$root" "$1" <<'PY'
import hashlib
import sys
from pathlib import Path

root, target = Path(sys.argv[1]), Path(sys.argv[2])
lines = []
for path in sorted((root / "raw").rglob("*")):
    if path.is_file():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(root).as_posix()}\n")
with target.open("w", encoding="utf-8", newline="\n") as f:
    f.writelines(lines)
print(f"{len(lines)} raw files hashed into {target.name}")
PY
}

echo "== 1. synthetic root, refresh, raw hashes"
av synth-logs --demo-seed "$seed" --out "$root"
raw_hashes "$out/raw-sha256-before.txt"
av refresh --root "$root" | tee "$out/refresh.txt"
raw_hashes "$out/raw-sha256-after.txt"
cmp "$out/raw-sha256-before.txt" "$out/raw-sha256-after.txt"
echo "raw files unchanged by the run"

echo "== 2. reconciliation time per visit"
py - "$root" "$out/timings.csv" <<'PY'
import sys
import time
from pathlib import Path

from av_analysis.loaders import raw_visit_ids
from av_analysis.paths import DataRoot
from av_analysis.reconcile import reconcile_visit

root = DataRoot.open(Path(sys.argv[1]))
rows = ["visit_id,seconds,status\n"]
worst = 0.0
for vid in raw_visit_ids(root):
    start = time.perf_counter()
    report = reconcile_visit(root, vid)
    seconds = time.perf_counter() - start
    worst = max(worst, seconds)
    rows.append(f"{vid},{seconds:.3f},{'pass' if report.passed else 'fail'}\n")
with open(sys.argv[2], "w", encoding="utf-8", newline="\n") as f:
    f.writelines(rows)
print(f"{len(rows) - 1} visits, slowest {worst:.3f} s")
if worst >= 30.0:
    sys.exit("a visit took 30 s or more")
PY

echo "== 3. fault-injection suite"
av synth-logs --demo-seed "$seed" --out "$out/fault-suite" --fault-suite
cp "$out/fault-suite/fault-suite.csv" "$out/fault-suite.csv"
