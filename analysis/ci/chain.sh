#!/usr/bin/env bash
# CI evidence for #6 (O4.5 integration demo); rules in analysis/ci/README.md.
# One SYNTHETIC study root carried through the whole chain in this job:
#  1. synth-logs: clean raw logs for every A and B visit type (one unit per study);
#  2. refresh: reconcile --all, derive and dashboard (masked: no keys/ yet); every visit
#     must pass with 0 discrepancies and raw files must stay byte-identical;
#  3. synth-logs --unseal-keys: the synthetic stand-in for unblinding (Study A book key);
#  4. run --study A and --study B on the same root (--glmm skip; GLMMs run in the r job).
# Outputs go to $AV_CI_OUT (uploaded as analysis-evidence-<os>). Synthetic DEMO data only.
set -euo pipefail

out="${AV_CI_OUT:?AV_CI_OUT is not set}"
seed="DEMO-o4.5-chain"
root="$out/root"

av() { uv run --project analysis --locked av-analysis "$@"; }
py() { uv run --project analysis --locked python "$@"; }

raw_hashes() {
  py - "$root" "$1" <<'PY'
import hashlib
import sys
from pathlib import Path

root, target = Path(sys.argv[1]), Path(sys.argv[2])
lines = [
    f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(root).as_posix()}\n"
    for p in sorted((root / "raw").rglob("*"))
    if p.is_file()
]
with target.open("w", encoding="utf-8", newline="\n") as f:
    f.writelines(lines)
print(f"{len(lines)} raw files hashed into {target.name}")
PY
}

echo "== 1. synthetic logs (A and B, pilot, one unit each)"
av synth-logs --demo-seed "$seed" --out "$root" --study both --set pilot
raw_hashes "$out/raw-sha256-before.txt"

echo "== 2. refresh: reconcile, derive, dashboard (masked)"
test ! -e "$root/keys"
av refresh --root "$root" | tee "$out/refresh.txt"
raw_hashes "$out/raw-sha256-after.txt"
cmp "$out/raw-sha256-before.txt" "$out/raw-sha256-after.txt"
py - "$root" <<'PY' | tee "$out/reconciliation.txt"
import csv
import sys
from pathlib import Path

root = Path(sys.argv[1])
with (root / "reconciled" / "visit-status.csv").open(newline="", encoding="utf-8") as f:
    visits = list(csv.DictReader(f))
with (root / "reconciled" / "discrepancies.csv").open(newline="", encoding="utf-8") as f:
    discrepancies = list(csv.DictReader(f))
studies = sorted({v["study"] for v in visits})
print(f"{len(visits)} visits reconciled (studies {', '.join(studies)}), "
      f"{len(discrepancies)} discrepancies")
failed = [v["visit_id"] for v in visits if v["reconciliation"] != "pass"]
if studies != ["A", "B"] or failed or discrepancies:
    sys.exit(f"chain: not clean (failed visits {failed}, {len(discrepancies)} discrepancies)")
PY
test -s "$root/monitoring/index.html"

echo "== 3. unblinding stand-in: DEMO unmasking key"
av synth-logs --demo-seed "$seed" --out "$root" --unseal-keys

echo "== 4. analysis on the same root"
for study in A B; do
  av run --study "$study" --data "$root" --set pilot --glmm skip > "$out/run-$study.txt"
  tail -n 3 "$out/run-$study.txt"
  test -s "$root/estimates/report-$study.md"
  cp "$root/estimates/report-$study.md" "$out/report-$study.md"
done
echo "chain complete: synth-logs -> reconcile -> derive -> dashboard -> run A and B"
