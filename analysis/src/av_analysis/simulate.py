"""Synthetic analysis datasets and operating characteristics (#34; analysis plan section 7).
Interface; #34 implements.

Ports the planning simulation's data-generating process (method/role/scaffold effects on
the logit scale with batch, book, person, item and interaction variance components and
attrition; central and pessimistic scenarios, null scenarios) and adds missingness and
fault patterns. Generates the derived tables directly (``derived.TRIALS`` and
``derived.ENDPOINTS`` rows, ``data_kind`` ``SYNTHETIC``) plus the unmasking key, into a
SYNTHETIC data root only (``paths``), with every seed stored.

``av-analysis simulate --scenario NAME --datasets N --seed DEMO-... --out DIR`` writes the
operating-characteristics CSV (rejection rates with Monte Carlo uncertainty); acceptance:
null scenario over 2,000 synthetic A datasets rejects at a rate between 0.040 and 0.060.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass

from .derived import Row


@dataclass(frozen=True)
class Scenario:
    """A data-generating scenario (planning simulation parameters plus missingness)."""

    name: str
    study: str
    effect_pp: float  # true primary contrast in percentage points (0 for null scenarios)
    baseline: float  # mean accuracy of the reference condition
    variances: Mapping[str, float]  # variance components on the logit scale
    attrition: float  # probability a person's primary endpoint is missing
    fault_rate: float  # probability an opportunity has a technical fault


def scenarios() -> Mapping[str, Scenario]:
    """Named scenarios (central, pessimistic, null for each study)."""
    raise NotImplementedError("#34: simulation scenarios")


def simulate_dataset(scenario: Scenario, seed: str) -> dict[str, list[Row]]:
    """One synthetic dataset: ``trials`` and ``endpoints`` rows (SYNTHETIC)."""
    raise NotImplementedError("#34: synthetic dataset")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Arguments of ``av-analysis simulate``."""
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--datasets", type=int, default=2000)
    parser.add_argument("--seed", required=True, help="DEMO- seed label")
    parser.add_argument("--out", required=True, help="SYNTHETIC data root")


def main(args: argparse.Namespace) -> int:
    """Run ``av-analysis simulate``."""
    raise NotImplementedError("#34")
