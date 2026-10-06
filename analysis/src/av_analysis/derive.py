"""Reconciled and derived tables of a data root (#33). Interface; #33 implements.

``av-analysis derive --root DIR`` reads every ``reconciled/<visit_id>/reconciliation.json``
with its raw logs and reference inputs and writes, through ``derived.table_bytes`` and
``paths.write_output``:

* ``reconciled/visit-status.csv``, ``reconciled/discrepancies.csv``,
  ``reconciled/exposure-cumulative.csv``, ``reconciled/enrollment.csv`` and
  ``reconciled/manifest.json``;
* ``derived/trials.csv``, ``derived/endpoints.csv`` and ``derived/manifest.json``
  (``outputs-manifest.schema.json``).

``visit-status`` has a row for every expected visit of every revealed person (pending
until held; ``missed`` or ``withdrawn`` from deviation records). ``trials`` adds a
``row_source`` deviation row for each scheduled opportunity lost to a verified apparatus
or logger failure (``reconcile``), and ``endpoints`` counts it as accounted and faulted;
opportunities never undertaken after withdrawal get no row and leave the battery partial
or missing (``withdrawn_mid_battery``). ``enrollment`` counts eligibility records and
reveals from the reveal log. Derived tables carry no condition labels; the dashboard (#35)
and the analysis pipeline (#34) read only these files, never raw logs. ``av-analysis
refresh`` (``cli``) runs reconcile, this command and the dashboard in order.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .derived import Row
from .paths import DataRoot


def derive_tables(root: DataRoot) -> dict[str, list[Row]]:
    """Table name (``derived.TABLES``) -> rows."""
    raise NotImplementedError("#33: derive the tables")


def write_tables(root: DataRoot, tables: dict[str, list[Row]]) -> list[Path]:
    """Write the tables and the two area manifests; returns the paths written."""
    raise NotImplementedError("#33: write the tables")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Arguments of ``av-analysis derive``."""
    parser.add_argument("--root", required=True, help="data root (av-data-root.json)")


def main(args: argparse.Namespace) -> int:
    """Run ``av-analysis derive``."""
    raise NotImplementedError("#33")
