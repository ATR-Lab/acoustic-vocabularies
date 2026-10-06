"""Per-visit reconciliation, checks C1-C8 (#33). Interface; #33 implements.

``av-analysis reconcile <visit_id>... --root DIR`` (or ``--all``) loads a raw visit
(``loaders``) and its reference inputs (``references``), runs the checks of
``codes.CHECKS`` and writes ``reconciled/<visit_id>/reconciliation.json``
(``reconciliation.schema.json``) through ``paths.write_output``. Rules:

* Raw files are opened read-only; their SHA-256 values are taken before and after the run
  and must be identical (``raw_unchanged``; acceptance criterion).
* Deterministic: no run time, inputs listed sorted by path, discrepancies in check order
  then by rows; the same inputs give identical bytes.
* Each discrepancy has a code (``codes.CODES``), the rows involved and a deviation link
  (trial-log ``deviation_id``, exposure-ledger ``matching_deviation_id``, or a deviations
  record whose ``event_id`` names the row, visit or person); otherwise it is unresolved
  and C8 reports ``DEVIATION_MISSING``. Check status: ``pass`` (none), ``explained`` (all
  linked), ``fail`` (any unresolved), ``not_applicable`` (study or visit without it).
* Never computes accuracy by condition, never writes outcome, response, response-time,
  rating or condition fields (``masking`` policy ``masked``).
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .codes import CheckId
from .loaders import RawVisit
from .paths import DataRoot
from .references import References
from .vocab import CheckStatus, DataKind


@dataclass(frozen=True)
class Discrepancy:
    """One discrepancy found by a check."""

    check: CheckId
    code: str
    rows: tuple[str, ...]  # trial_id, event_id or file names
    deviation_id: str | None
    resolved: bool
    detail: str  # generated text: rows and rule only, never scores or conditions


@dataclass(frozen=True)
class CheckResult:
    """Result of one check."""

    check: CheckId
    status: CheckStatus
    discrepancies: tuple[Discrepancy, ...]


@dataclass(frozen=True)
class Report:
    """A visit's reconciliation report (``reconciliation.schema.json``)."""

    data_kind: DataKind
    study: str
    set_name: str
    unit_id: str
    person_id: str
    visit: str
    session_id: str | None
    station_id: str | None
    inputs: tuple[tuple[str, int, str], ...]  # (path, bytes, sha256), sorted by path
    raw_unchanged: bool
    checks: tuple[CheckResult, ...]  # C1..C8 in order

    def document(self) -> dict[str, Any]:
        """The JSON document of the report."""
        raise NotImplementedError("#33: report document")


def run_checks(raw: RawVisit, refs: References, root: DataRoot) -> tuple[CheckResult, ...]:
    """Checks C1-C8 of one visit, in order (C8 last, over the other seven)."""
    raise NotImplementedError("#33: checks C1-C8")


def reconcile_visit(root: DataRoot, visit_id: str) -> Report:
    """Reconcile one visit (raw files read-only, hashed before and after)."""
    raise NotImplementedError("#33: reconcile one visit")


def write_report(root: DataRoot, report: Report) -> Path:
    """Write ``reconciled/<visit_id>/reconciliation.json``."""
    raise NotImplementedError("#33: write the report")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Arguments of ``av-analysis reconcile``."""
    parser.add_argument("visit_ids", nargs="*", help="visit IDs such as A-C07-L03-D0")
    parser.add_argument("--root", required=True, help="data root (av-data-root.json)")
    parser.add_argument("--all", action="store_true", help="every visit folder under raw/")


def main(args: argparse.Namespace) -> int:
    """Run ``av-analysis reconcile``: exit 0 when every visit passes, 1 otherwise."""
    raise NotImplementedError("#33")


def reconcile_many(root: DataRoot, visit_ids: Sequence[str]) -> list[Report]:
    """Reconcile several visits in order (one report each)."""
    raise NotImplementedError("#33: reconcile several visits")
