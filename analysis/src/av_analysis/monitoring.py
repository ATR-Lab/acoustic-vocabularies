"""Integrity monitoring dashboard (#35; analysis plan section 8). Interface; #35 implements.

``av-analysis dashboard --root DIR`` regenerates ``monitoring/index.html`` (static HTML and
CSS, no JavaScript from outside, no network) from the reconciled tables only:
``reconciled/visit-status.csv`` and ``reconciled/discrepancies.csv`` (``derived``). It
shows the last update time as the latest visit date and the SHA-256 of the input tables
(deterministic; no wall clock).

Hard rule: no accuracy, response, response-time or rating field and no method, role or
scaffold split ever reaches the rendering layer. The loader keeps only allow-listed
columns (:func:`allowlist`), every allow-listed column must pass
``masking.forbidden_reason(column, "masked")``, free-text template columns are never
rendered, and a build that meets a disallowed column fails (:class:`DisallowedColumnError`).
Faults are pooled and by station, never by condition. Coded IDs only.

Panels (:data:`PANELS`, issue #35): enrollment against frozen targets
(``av_schedules.planning`` ``ALLOCATION_CHECKS`` / ``PILOT_ALLOCATION_COUNTS``; final
targets from the sample-size decisions), allocation progress per batch or dyad, attrition
and missed visits, window adherence (``windows``), faults by type against
``vocab.FAULT_RATE_TRIGGER`` and overruns against ``vocab.OVERRUN_*``, reconciliation
status and open deviations, and red alerts for the three suspension events
(``codes.SUSPENSION_EVENTS``) with the affected visit IDs.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .derived import Row
from .paths import DataRoot


@dataclass(frozen=True)
class Panel:
    """One dashboard panel and the reconciled tables it reads."""

    id: str
    title: str
    sources: tuple[str, ...]  # derived.TABLES names


PANELS: Final[tuple[Panel, ...]] = (
    Panel("alerts", "Suspension alerts", ("discrepancies", "visit-status")),
    Panel("enrollment", "Enrollment against frozen targets", ("visit-status",)),
    Panel("allocation", "Allocation progress per batch or dyad", ("visit-status",)),
    Panel("attrition", "Attrition and missed visits", ("visit-status",)),
    Panel("windows", "Visit-window adherence", ("visit-status",)),
    Panel("faults", "Faults by type and station; visit overruns", ("visit-status",)),
    Panel(
        "reconciliation",
        "Reconciliation status, open deviations, comfort and welfare",
        ("visit-status", "discrepancies"),
    ),
)
SOURCE_TABLES: Final[tuple[str, ...]] = ("visit-status", "discrepancies")


class DisallowedColumnError(RuntimeError):
    """A table or panel offered a column outside the allowlist, or a forbidden column."""


@dataclass(frozen=True)
class MonitoringData:
    """Allow-listed rows of the source tables, as handed to the renderer."""

    data_kind: str
    tables: Mapping[str, tuple[Row, ...]]
    inputs: Mapping[str, str]  # table file -> SHA-256


def allowlist() -> Mapping[str, frozenset[str]]:
    """Source table -> columns the dashboard may read."""
    raise NotImplementedError("#35: column allowlist")


def load_monitoring_data(root: DataRoot) -> MonitoringData:
    """Read the source tables, keep allow-listed columns, refuse anything else."""
    raise NotImplementedError("#35: load reconciled tables")


def render(data: MonitoringData) -> str:
    """The dashboard HTML (watermarked, see ``paths.check_watermark``)."""
    raise NotImplementedError("#35: render the panels")


def write_dashboard(root: DataRoot) -> Path:
    """Regenerate ``monitoring/index.html``."""
    raise NotImplementedError("#35: write the dashboard")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Arguments of ``av-analysis dashboard``."""
    parser.add_argument("--root", required=True, help="data root (av-data-root.json)")


def main(args: argparse.Namespace) -> int:
    """Run ``av-analysis dashboard``."""
    raise NotImplementedError("#35")
