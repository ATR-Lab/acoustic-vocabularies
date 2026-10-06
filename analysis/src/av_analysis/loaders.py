"""Raw-log loaders with validation (#33). Interface; #33 implements.

Loads the four methodology logs of a raw visit folder (``paths``: ``raw/<visit_id>/``)
and the study-wide ``raw/deviations-log.csv``, opening every file read-only
(``fileio.read_bytes``). A file must decode as UTF-8 (BOM allowed), its header must equal
the template (``templates.TEMPLATES``) optionally followed by the template's extension
columns (``templates.EXTENSION_COLUMNS``, in order; other headset-specific columns only
once listed: **Pending** ADR-007), and every value must be in its domain (``vocab``:
playback and audible status, response codes, ``;``-joined fault codes, booleans
``true``/``false``, integer milliseconds, ISO 8601 times with a UTC offset, coded staff
IDs, comfort checks, deviation categories). Problems are returned as ``RowProblem``
values and become ``RAW_FORMAT`` discrepancies (C1); a loader never repairs a value. In a
REAL root, a visit whose exit manifest says ``SYNTHETIC``, whose exit manifest ``source``
is missing or has an unacknowledged torn tail, or whose IDs carry a ``DEMO-`` or
``SYNTHETIC`` marker is refused.

The provisional station export (#72 ``data-csv-provisional-1``) uses other headers
(``coded_id``, ``opportunity_id``/``attempt_id``, ...). Its values already follow
``vocab``; the column adapter to the template headers is **Pending** (#72/#73 with #33)
and runs before files are placed in ``raw/``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import DataRoot
from .templates import Template, TemplateName


@dataclass(frozen=True)
class RowProblem:
    """A value outside its domain (or a structural problem when ``line`` is 1)."""

    file: str  # path relative to the data root
    line: int  # 1 = header
    column: str | None
    message: str


@dataclass(frozen=True)
class LoadedTable:
    """One template CSV as read: raw strings, never modified."""

    template: Template
    path: str  # relative to the data root
    sha256: str
    rows: tuple[Mapping[str, str], ...]
    problems: tuple[RowProblem, ...]


@dataclass(frozen=True)
class RawVisit:
    """Everything in ``raw/<visit_id>/``."""

    visit_id: str
    trial_log: LoadedTable | None
    exposure_ledger: LoadedTable | None
    run_sheet: LoadedTable | None
    deviations: LoadedTable | None
    exit_manifest: Mapping[str, Any] | None
    files: Mapping[str, str]  # every file in the folder -> SHA-256 at load time


def value_problems(template: Template, row: Mapping[str, str]) -> list[str]:
    """Domain problems of one row (``column: message``), empty when valid."""
    raise NotImplementedError("#33: value domains of the template columns")


def load_template_csv(root: DataRoot, path: Path, name: TemplateName) -> LoadedTable:
    """Read and validate one template CSV (read-only)."""
    raise NotImplementedError("#33: template CSV loader")


def load_raw_visit(root: DataRoot, visit_id: str) -> RawVisit:
    """Load a raw visit folder; missing files are ``None`` (C1 reports them)."""
    raise NotImplementedError("#33: raw visit loader")


def load_deviations_log(root: DataRoot) -> LoadedTable | None:
    """Load ``raw/deviations-log.csv`` (append-only; C8 verifies the earlier prefix)."""
    raise NotImplementedError("#33: study-wide deviations log loader")
