#!/usr/bin/env bash
# CI evidence for #35, the integrity monitoring dashboard (rules: analysis/ci/README.md).
# Synthetic DEMO data only; every output goes below $AV_CI_OUT (uploaded as the artifact
# analysis-evidence-<os>). No screenshot or other binary is committed.
set -euo pipefail

out="${AV_CI_OUT:?AV_CI_OUT is set by the workflow}"
mkdir -p "$out"
abs_out="$(cd "$out" && pwd)"
run() { uv run --project analysis --locked "$@"; }

echo "== 1. Full-size synthetic reconciled tables with the three suspension events"
demo="$out/demo-root"
rm -rf "$demo"
run python -m av_analysis.monitoring_demo --out "$demo" --seed DEMO-ci-35 \
  --study both --set both --progress 0.8 \
  --inject wrong_hash --inject changed_old_atom --inject answer_leak

echo "== 2. Regenerate the dashboard and time it (exit 1 = red alerts, expected here)"
TIMEFORMAT='%R'
set +e
{ time run av-analysis dashboard --root "$demo" \
  >"$out/dashboard-stdout.txt" 2>"$out/dashboard-stderr.txt"; } 2>"$out/dashboard-seconds.txt"
code=$?
set -e
cat "$out/dashboard-stdout.txt"
if [ "$code" -ne 1 ]; then
  echo "expected exit 1 (red alerts), got $code"
  cat "$out/dashboard-stderr.txt"
  exit 1
fi
for event in WRONG_FILE_MAPPING ANSWER_LEAK OLD_WAVEFORM_CHANGED; do
  grep -q "RED ALERT $event" "$out/dashboard-stdout.txt" || { echo "no red alert $event"; exit 1; }
done
seconds="$(tr -d '[:space:]' <"$out/dashboard-seconds.txt")"
echo "regeneration on full-size synthetic data, uv start-up included: ${seconds} s (limit 60 s)"
awk -v s="$seconds" 'BEGIN { exit !(s + 0 < 60) }'

echo "== 3. Summary of the metrics (dashboard.json)"
run python - "$demo/monitoring/dashboard.json" "$seconds" <<'PY' | tee "$out/summary.txt"
import json
import sys

doc = json.load(open(sys.argv[1], encoding="utf-8"))
print(f"data kind {doc['data_kind']}; data as of {doc['as_of']['data_date']}")
print(f"regeneration seconds (uv start-up included): {sys.argv[2]}")
for e in doc["enrollment"]:
    print(
        f"enrollment {e['study']} {e['set']}: {e['revealed_persons_n']} of "
        f"{e['target_persons_n']} persons, {e['revealed_units_n']} of {e['target_units_n']} units"
    )


def share(value):
    return "n/a" if value is None else f"{value:.3%}"


for scope in (doc["faults"]["pooled"], *doc["faults"]["by_group"], *doc["faults"]["by_station"]):
    name = scope.get("station_id") or " ".join(filter(None, (scope.get("study"), scope.get("set"))))
    print(
        f"faults {name or 'all'}: {scope['fault_n']} of {scope['opportunities_n']} "
        f"({share(scope['fault_rate'])}) trigger exceeded {scope['trigger_exceeded']}"
    )
for scope in (doc["overruns"]["pooled"], *doc["overruns"]["by_group"]):
    name = " ".join(filter(None, (scope.get("study"), scope.get("set"))))
    print(
        f"overruns {name or 'all'}: {scope['overrun_n']} of {scope['checked_n']} "
        f"({share(scope['overrun_share'])}) trigger exceeded {scope['trigger_exceeded']}"
    )
for alert in doc["alerts"]["suspension"]:
    print(f"RED {alert['event']}: {', '.join(alert['visit_ids'])} ({', '.join(alert['codes'])})")
for trigger in doc["alerts"]["triggers"]:
    print(f"amber {trigger['trigger']} {trigger['study']} {trigger['set']}: {trigger['message']}")
PY

echo "== 4. Clean synthetic dashboard (no suspension event: exit 0)"
clean="$out/clean-root"
rm -rf "$clean"
run python -m av_analysis.monitoring_demo --out "$clean" --seed DEMO-ci-35-clean \
  --study both --set confirmatory --progress 0.7
run av-analysis dashboard --root "$clean"

echo "== 5. Reconciliation chain (#33): synth-logs, then refresh (skipped until #33 lands)"
chain="$out/chain-root"
rm -rf "$chain"
set +e
run av-analysis synth-logs --demo-seed DEMO-ci-35 --out "$chain" >"$out/chain.txt" 2>&1
code=$?
set -e
if [ "$code" -eq 3 ]; then
  echo "synth-logs is not implemented on this branch yet: chain skipped" | tee -a "$out/chain.txt"
elif [ "$code" -ne 0 ]; then
  cat "$out/chain.txt"
  exit 1
else
  set +e
  run av-analysis refresh --root "$chain" >>"$out/chain.txt" 2>&1
  code=$?
  set -e
  cat "$out/chain.txt"
  if [ "$code" -ge 2 ]; then
    exit 1
  fi
fi

echo "== 6. Screenshots (Linux runner, headless Chrome)"
if [ "${RUNNER_OS:-}" = "Linux" ]; then
  chrome="$(command -v google-chrome || command -v google-chrome-stable || command -v chromium \
    || command -v chromium-browser || true)"
  if [ -z "$chrome" ]; then
    echo "no Chrome on this runner: screenshots skipped"
  else
    page="file://$(cd "$demo/monitoring" && pwd)/index.html"
    for shot in "top:1280,1800" "full:1280,16000"; do
      name="${shot%%:*}"
      "$chrome" --headless=new --no-sandbox --disable-gpu --hide-scrollbars \
        --window-size="${shot#*:}" --screenshot="$abs_out/dashboard-$name.png" "$page" \
        >/dev/null 2>&1
      test -s "$abs_out/dashboard-$name.png"
      echo "screenshot $out/dashboard-$name.png"
    done
  fi
else
  echo "screenshots run on the Linux runner only"
fi
