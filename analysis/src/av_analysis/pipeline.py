"""``av-analysis run --study A|B --data DIR`` (#34). Interface; #34 implements.

Reads ``derived/`` (and ``reconciled/visit-status.csv`` for flow and fidelity), refuses
derived tables whose visits did not pass reconciliation unless explained by deviations,
joins conditions (``unmask``), scores (``scoring``), estimates (``estimators``,
``missingness``, ``glmm``) and writes every section 9 output (``report``) into
``estimates/`` of the same data root, with ``estimates/manifest.json``. Synthetic roots
give SYNTHETIC-watermarked outputs; a run never writes outside its root.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .paths import DataRoot


def run_analysis(root: DataRoot, study: str) -> list[Path]:
    """All section 9 outputs of one study; returns the files written."""
    raise NotImplementedError("#34: analysis pipeline")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Arguments of ``av-analysis run``."""
    parser.add_argument("--study", choices=("A", "B"), required=True)
    parser.add_argument("--data", required=True, help="data root (av-data-root.json)")


def main(args: argparse.Namespace) -> int:
    """Run ``av-analysis run``."""
    raise NotImplementedError("#34")
