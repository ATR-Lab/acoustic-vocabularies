"""Synthetic raw logs from the schedules and run sheets, and fault injection (#33).
Interface; #33 implements.

``av-analysis synth-logs --demo-seed DEMO-... --out DIR [--study A|B|both]
[--set pilot|confirmatory] [--fault NAME --visit VISIT_ID]`` creates a SYNTHETIC data root
(``paths.DataRoot.create``): ``inputs/`` from ``av_schedules`` DEMO sets (``checks.build_set``
gives schedules, allocation lists and run sheets) plus synthetic package JSON, and
``raw/<visit_id>/`` with the four template CSVs and an exit manifest for every visit type
(A D0, D7; B V1, V2, V3, W1, W4, both dyad members, yoked within 24 h). Clean logs must
reconcile with zero discrepancies. Responses are synthetic placeholders drawn from
``seeds.rng`` (no learning model; #34's ``simulate`` owns outcome models); IDs and
session IDs carry a ``SYNTHETIC`` marker.

Fault injection: ``inject_fault`` applies one fault of ``codes.FAULT_INJECTIONS`` to a
visit of a synthetic root (rewriting the raw files and their exit manifest consistently,
except where the fault is the manifest itself); reconciliation must report the mapped
code.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .paths import DataRoot


def build_synthetic_root(
    path: Path,
    *,
    seed_label: str,
    study: str = "both",
    set_name: str = "pilot",
) -> DataRoot:
    """Create a SYNTHETIC data root with inputs and clean raw logs for every visit."""
    raise NotImplementedError("#33: synthetic data root")


def synthetic_visit_files(root: DataRoot, visit_id: str) -> dict[str, bytes]:
    """The raw files of one clean synthetic visit (file name -> bytes)."""
    raise NotImplementedError("#33: synthetic raw logs of one visit")


def inject_fault(root: DataRoot, visit_id: str, fault: str) -> None:
    """Inject one fault (a key of ``codes.FAULT_INJECTIONS``) into a synthetic visit."""
    raise NotImplementedError("#33: fault injection")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Arguments of ``av-analysis synth-logs``."""
    parser.add_argument("--demo-seed", required=True, help="public DEMO- seed label")
    parser.add_argument("--out", required=True, help="new SYNTHETIC data root")
    parser.add_argument("--study", choices=("A", "B", "both"), default="both")
    parser.add_argument("--set", choices=("pilot", "confirmatory"), default="pilot")
    parser.add_argument("--fault", help="inject one fault (codes.FAULT_INJECTIONS)")
    parser.add_argument("--visit", help="visit ID for --fault")


def main(args: argparse.Namespace) -> int:
    """Run ``av-analysis synth-logs``."""
    raise NotImplementedError("#33")
