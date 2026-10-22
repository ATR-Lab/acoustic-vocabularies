"""Replay a diagnostic probe series against the unchanged 250 ms effective-age rule.

For consecutive accepted probes i and i+1 (same connection), the native gate's
largest effective age before reply i+1 lands is
  (complete[i+1] - complete[i]) + rtt[i] + max(neutral_age[i], publisher_age[i]),
i.e. ControlHealthGate.Fresh evaluated an instant before the next receipt.
This is a derived exposure-window estimate for comparing routes, not a native
observation; it does not include Unity main-thread pumping or queue age.
"""
import argparse, json
from pathlib import Path


def windows(rows):
    ok = [r for r in rows if 'rtt_ms' in r and 'publisher_age_ms' in r]
    out = []
    for a, b in zip(ok, ok[1:]):
        if a.get('connection') != b.get('connection') or b['index'] != a['index'] + 1: continue
        source = max(x if x is not None else float('inf') for x in (a['neutral_age_ms'], a['publisher_age_ms']))
        out.append(dict(index=a['index'], gap_ms=(b['complete_ns'] - a['complete_ns']) / 1e6, rtt_ms=a['rtt_ms'],
                        source_age_ms=source, worst_effective_ms=(b['complete_ns'] - a['complete_ns']) / 1e6 + a['rtt_ms'] + source))
    return out


def q(v, f):
    s = sorted(v); return round(s[min(len(s) - 1, int(len(s) * f))], 3) if s else None


def main():
    p = argparse.ArgumentParser(); p.add_argument('clients', type=Path, nargs='+'); a = p.parse_args()
    for path in a.clients:
        d = json.loads(path.read_text()); w = windows(d['rows'])
        eff = [x['worst_effective_ms'] for x in w]; src = [x['source_age_ms'] for x in w]
        print(json.dumps(dict(label=d['summary']['label'], intervals=len(w), deadline_failures=d['summary']['failures'],
            worst_effective_median=q(eff, .5), worst_effective_p99=q(eff, .99), worst_effective_max=q(eff, 1),
            intervals_over_250=sum(x > 250 for x in eff), source_age_median=q(src, .5), source_age_max=q(src, 1))))


if __name__ == '__main__':
    main()
