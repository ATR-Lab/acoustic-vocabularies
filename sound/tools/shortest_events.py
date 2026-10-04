"""Tabulate per-event sample counts for every timing structure (renderer spec D1).

Writes one CSV row per combination of total_ms, rhythm weights and gaps
(4 x 64 x 9 = 2,304 rows). Uses only the standard library so it can run
before the renderer package exists.

Usage: python sound/tools/shortest_events.py [output.csv]
"""

from __future__ import annotations

import csv
import itertools
import sys
from pathlib import Path

SAMPLES_PER_MS = 48
MIN_EVENT_SAMPLES = 60 * SAMPLES_PER_MS  # 2,880
TOTALS_MS = (450, 600, 750, 900)
WEIGHTS = (1, 2, 3, 4)
GAPS_MS = (20, 40, 60)


def event_samples(total_ms: int, weights: tuple[int, int, int], gaps_ms: tuple[int, int]) -> tuple[int, int, int]:
    """Spec D1: events 1 and 2 rounded half-up, event 3 takes the remainder."""
    d = (total_ms - gaps_ms[0] - gaps_ms[1]) * SAMPLES_PER_MS
    w = sum(weights)
    n1 = (2 * d * weights[0] + w) // (2 * w)
    n2 = (2 * d * weights[1] + w) // (2 * w)
    return n1, n2, d - n1 - n2


def main(out: Path) -> None:
    header = ["total_ms", "w1", "w2", "w3", "g1_ms", "g2_ms", "n1", "n2", "n3",
              "shortest_samples", "shortest_ms", "admissible"]
    admissible: dict[int, int] = dict.fromkeys(TOTALS_MS, 0)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(header)
        for t in TOTALS_MS:
            for w in itertools.product(WEIGHTS, repeat=3):
                for g in itertools.product(GAPS_MS, repeat=2):
                    n = event_samples(t, w, g)
                    assert sum(n) + (g[0] + g[1]) * SAMPLES_PER_MS == t * SAMPLES_PER_MS
                    shortest = min(n)
                    ok = shortest >= MIN_EVENT_SAMPLES
                    admissible[t] += ok
                    ms = f"{shortest // SAMPLES_PER_MS}.{(shortest % SAMPLES_PER_MS) * 1000 // SAMPLES_PER_MS:03d}"
                    writer.writerow([t, *w, *g, *n, shortest, ms, int(ok)])
    total = sum(admissible.values())
    print(f"wrote {out}: 2304 timing structures, {total} admissible")
    for t, k in admissible.items():
        print(f"  total_ms={t}: {k}/576 admissible")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("sound/docs/data/shortest-event-samples.csv"))
