"""#309 full reproduction of the earlier planning simulation values (logistic-normal
engine, committed planning example, 2,000 replicates per cell).

Runs only with ``AV_POWER_FULL=1`` (about 15 s; skipped in CI):

    AV_POWER_FULL=1 uv run --project analysis pytest --import-mode=importlib \\
        -p no:cacheprovider tests/analysis/test_power_check_reproduction.py -s

Each rate is compared by ``z = (ours - planning) / sqrt(mcse_ours^2 + mcse_planning^2)``
with the planning MCSE ``sqrt(p (1 - p) / n)`` at the planning replicate count; |z| <= 3
passes here (|z| <= 2 counts as reproduced in the report). Mean implied unit SDs (planning
values rounded to .001) use the MCSE of our mean SD for both runs plus the rounding
variance. Nothing here tunes the model or the seed.
"""

from __future__ import annotations

import math
import os

import pytest

from av_analysis.power_check import cells, load_config, planning_example_path, run_cell

pytestmark = pytest.mark.skipif(
    os.environ.get("AV_POWER_FULL") != "1",
    reason="full reproduction of the planning values: set AV_POWER_FULL=1",
)

Z_TOLERANCE = 3.0
# (label, study, SD source, scenario, loss, size, contrast, planning value, planning reps)
RATES = [
    ("A central", "A", "central", "effect", 0.05, 18, "A3-A2", 0.852, 2000),
    ("A pessimistic", "A", "pessimistic", "effect", 0.05, 18, "A3-A2", 0.604, 2000),
    ("A pessimistic", "A", "pessimistic", "effect", 0.05, 24, "A3-A2", 0.709, 1000),
    ("A pessimistic", "A", "pessimistic", "effect", 0.05, 30, "A3-A2", 0.848, 1000),
    ("A pessimistic", "A", "pessimistic", "effect", 0.05, 36, "A3-A2", 0.895, 1000),
    ("A null, central", "A", "central", "null", 0.05, 18, "A3-A2", 0.046, 2000),
    ("B central, both", "B", "central", "teaching-present", 0.15, 64, "C", 0.952, 2000),
    ("B central, both", "B", "central", "teaching-present", 0.15, 64, "S", 1.000, 2000),
    ("B pessimistic, both", "B", "pessimistic", "teaching-present", 0.15, 64, "C", 0.832, 2000),
    ("B pessimistic, both", "B", "pessimistic", "teaching-present", 0.15, 64, "S", 0.996, 2000),
    ("B pessimistic, both", "B", "pessimistic", "teaching-present", 0.25, 64, "C", 0.737, 2000),
    ("B pessimistic, both", "B", "pessimistic", "teaching-present", 0.25, 64, "S", 0.969, 2000),
    ("B pessimistic, both", "B", "pessimistic", "teaching-present", 0.15, 80, "C", 0.927, 1000),
    ("B pessimistic, both", "B", "pessimistic", "teaching-present", 0.25, 80, "C", 0.841, 1000),
    ("B central, no teaching", "B", "central", "teaching-absent", 0.15, 64, "C", 0.857, 2000),
    ("B pess., no teaching", "B", "pessimistic", "teaching-absent", 0.15, 64, "C", 0.684, 2000),
    ("B pess., no teaching", "B", "pessimistic", "teaching-absent", 0.15, 80, "C", 0.799, 2000),
    ("B pess., no teaching", "B", "pessimistic", "teaching-absent", 0.15, 84, "C", 0.814, 2000),
    ("B pess., no teaching", "B", "pessimistic", "teaching-absent", 0.15, 88, "C", 0.828, 2000),
    ("B global null, central", "B", "central", "global-null", 0.15, 64, "any", 0.042, 2000),
]  # fmt: skip
# Mean implied unit SD (A at 18 batches; B at 64 pairs, both effects, 15% loss).
SDS = [
    ("A central", "A", "central", "effect", 0.05, 18, "A3-A2", 0.131),
    ("A pessimistic", "A", "pessimistic", "effect", 0.05, 18, "A3-A2", 0.180),
    ("C central", "B", "central", "teaching-present", 0.15, 64, "C", 0.182),
    ("C pessimistic", "B", "pessimistic", "teaching-present", 0.15, 64, "C", 0.222),
    ("S central", "B", "central", "teaching-present", 0.15, 64, "S", 0.120),
    ("S pessimistic", "B", "pessimistic", "teaching-present", 0.15, 64, "S", 0.147),
]
ROUNDING_VARIANCE = 0.001**2 / 12.0


def _z(ours, mcse_ours, planning, reps):
    se = math.sqrt(mcse_ours**2 + planning * (1.0 - planning) / reps)
    if se == 0.0:
        return 0.0 if ours == planning else math.inf
    return (ours - planning) / se


def test_full_reproduction_of_the_earlier_planning_simulation_values():
    config = load_config(planning_example_path())
    assert config.replicates == 2000
    wanted = {(r[1], r[2], r[3], r[4], r[5]) for r in [*RATES, *SDS]}
    rows = {}
    for cell in cells(config):
        key = (cell.grid.study, cell.heterogeneity.name, cell.scenario.name, cell.loss, cell.size)
        if key in wanted:
            rows.update({(*key, r.contrast): r for r in run_cell(config, cell)})
    failures = []
    print("\nrow | size | planning | ours | MCSE | z")
    for label, *key, planning, reps in RATES:
        r = rows[tuple(key)]
        z = _z(r.rate, r.mcse, planning, reps)
        line = f"{label} {key[-1]} | {key[-2]} | {planning:.3f} | {r.rate:.4f} | {r.mcse:.4f}"
        print(f"{line} | {z:+.2f}")
        if abs(z) > Z_TOLERANCE:
            failures.append((label, *key, planning, r.rate, z))
    for label, *key, planning in SDS:
        r = rows[tuple(key)]
        z = (r.implied_unit_sd - planning) / math.sqrt(
            2.0 * r.implied_unit_sd_mcse**2 + ROUNDING_VARIANCE
        )
        sd, sd_se = r.implied_unit_sd, r.implied_unit_sd_mcse
        print(f"{label} SD | {key[-2]} | {planning:.3f} | {sd:.4f} | {sd_se:.4f} | {z:+.2f}")
        if abs(z) > Z_TOLERANCE:
            failures.append((label, *key, planning, r.implied_unit_sd, z))
    assert not failures, failures
