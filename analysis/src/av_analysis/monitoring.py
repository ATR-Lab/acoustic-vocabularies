"""Integrity monitoring dashboard (#35; analysis plan section 8).

``av-analysis dashboard --root DIR`` regenerates ``monitoring/index.html`` (static HTML and
CSS, no script, no network), ``monitoring/dashboard.json`` (the panel metrics,
``dashboard-data.schema.json``) and ``monitoring/manifest.json`` from the reconciled
tables only: ``reconciled/visit-status.csv``, ``reconciled/discrepancies.csv`` and
``reconciled/enrollment.csv`` (``derived``). It shows the last update as the latest visit
and reveal-log dates and the SHA-256 of the input tables (deterministic; no wall clock).
After each reconciliation run the operator runs ``av-analysis refresh --root DIR``
(``cli``: reconcile ``--all``, derive, dashboard), so the dashboard is regenerated without
#33's commands importing this module.

Hard rule: no accuracy, response, response-time or rating field and no method, role or
scaffold split ever reaches the rendering layer. Three gates:

1. :func:`load_monitoring_data` reads each table's header first. Every column must be
   allow-listed (:data:`ALLOWED_COLUMNS`) or a reviewed exclusion (:data:`EXCLUDED_COLUMNS`,
   dropped at load); a column that ``masking.forbidden_reason(column, "masked")`` names, a
   free-text template column or any other column fails the build
   (:class:`DisallowedColumnError`) before a row is parsed and before anything is written.
2. :func:`check_monitoring_data` refuses any row key outside the allowlist (also for
   :class:`MonitoringData` built in memory) before metrics are computed.
3. The metrics document (``monitoring_metrics``) is validated against its strict schema
   and the ``masked`` deny list; the HTML renderer (``monitoring_html``) sees only it.

Faults are pooled and by station, never by condition. Coded IDs only.

Panels (:data:`PANELS`, issue #35): suspension alerts, enrollment against frozen targets,
allocation progress per batch or dyad, attrition and missed visits, window adherence,
faults by type and station with overruns, and reconciliation status with open deviations
and comfort and withdrawal reports. Exit codes of ``dashboard``: 0 written, 1 written with
red alerts (suspension events), 2 refused input (no data root, missing or malformed table,
disallowed column).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Final

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from . import __version__
from .derived import TABLES, Row, TableFormatError, parse_table
from .fileio import CsvFormatError, json_bytes, parse_csv, read_bytes, sha256_bytes
from .masking import forbidden_keys, forbidden_reason, free_text_columns
from .monitoring_html import render_document
from .monitoring_metrics import SCHEMA_NAME, build_document, dashboard_data_schema
from .paths import OUTPUTS_MANIFEST, DataRoot, WatermarkError, write_output
from .schemas import OUTPUTS_MANIFEST_FORMAT, OUTPUTS_MANIFEST_FORMAT_VERSION
from .vocab import DATA_KINDS, FAULT_TYPES, DataKind


@dataclass(frozen=True)
class Panel:
    """One dashboard panel and the reconciled tables it reads."""

    id: str
    title: str
    sources: tuple[str, ...]  # derived.TABLES names


PANELS: Final[tuple[Panel, ...]] = (
    Panel("alerts", "Suspension alerts", ("discrepancies", "visit-status")),
    Panel("enrollment", "Enrollment against frozen targets", ("enrollment", "visit-status")),
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
SOURCE_TABLES: Final[tuple[str, ...]] = ("visit-status", "discrepancies", "enrollment")
OUTPUT_FILES: Final[tuple[str, ...]] = ("index.html", "dashboard.json", OUTPUTS_MANIFEST)

# The columns the dashboard reads, per source table (everything else never leaves the
# loader). Each one passes masking policy "masked" and none is a free-text column.
ALLOWED_COLUMNS: Final[dict[str, tuple[str, ...]]] = {
    "visit-status": (
        "data_kind",
        "study",
        "set",
        "unit_id",
        "person_id",
        "visit",
        "visit_id",
        "station_id",
        "visit_state",
        "reconciliation",
        "checks_failed",
        "discrepancies_n",
        "unresolved_n",
        "suspension_events",
        "visit_date",
        "days_since_anchor",
        "window_lo_days",
        "window_hi_days",
        "timing",
        "pair_gap_hours",
        "pair_gap_ok",
        "overrun",
        "opportunities_n",
        "fault_n",
        *(f"fault_{t}_n" for t in FAULT_TYPES),
        "comfort_flag",
        "deviations_n",
        "open_deviations_n",
        "comfort_deviations_n",
        "withdrawal_deviations_n",
    ),
    "discrepancies": (
        "data_kind",
        "study",
        "visit_id",
        "seq",
        "check",
        "code",
        "resolved",
        "suspension_event",
    ),
    "enrollment": (
        "data_kind",
        "study",
        "set",
        "planned_units_n",
        "planned_persons_n",
        "eligibility_records_n",
        "eligible_persons_n",
        "screening_cases_n",
        "revealed_units_n",
        "revealed_persons_n",
        "spares_used_n",
        "bank_unavailable_n",
        "last_event_date",
    ),
}
# Reviewed columns of the source tables that the dashboard does not read (dropped at load).
EXCLUDED_COLUMNS: Final[dict[str, dict[str, str]]] = {
    "visit-status": {
        "visit_seq": "ordering only; visits are ordered by the study's visit list",
        "anchor_visit": "implied by windows.WINDOWS",
        "booked_minutes": "per-visit durations are not shown: Study B active and yoked "
        "sessions differ in content, so durations could hint at a member's condition; the "
        "overrun flag is shown in aggregate",
        "actual_minutes": "as booked_minutes",
        "report_sha256": "audit detail of the per-visit report",
    },
    "discrepancies": {
        "unit_id": "implied by visit_id",
        "person_id": "implied by visit_id",
        "visit": "implied by visit_id",
        "visit_seq": "ordering only",
        "rows": "trial and event IDs are audit detail",
        "deviation_id": "audit detail; resolved carries the link status",
        "detail": "generated text; the page shows code titles from codes.code() instead, "
        "so no text from the tables reaches the page",
    },
    "enrollment": {"reveal_log_sha256": "audit detail; the input table hashes are shown"},
}


class DisallowedColumnError(RuntimeError):
    """A table or panel offered a column outside the allowlist, or a forbidden column."""


class MonitoringInputError(ValueError):
    """A source table is missing or does not follow its specification."""


@dataclass(frozen=True)
class MonitoringData:
    """Allow-listed rows of the source tables, as handed to the renderer."""

    data_kind: str
    tables: Mapping[str, tuple[Row, ...]]
    inputs: Mapping[str, str]  # table file -> SHA-256


def allowlist() -> Mapping[str, frozenset[str]]:
    """Source table -> columns the dashboard may read."""
    return {table: frozenset(columns) for table, columns in ALLOWED_COLUMNS.items()}


def disallowed_columns(table: str, columns: Iterable[str]) -> dict[str, str]:
    """``{column: reason}`` for every column of ``table`` the dashboard refuses: a masked
    field (``masking`` reason), a free-text template column, or a column that is neither
    allow-listed nor a reviewed exclusion (``not allow-listed``)."""
    allowed = allowlist().get(table, frozenset())
    excluded = EXCLUDED_COLUMNS.get(table, {})
    free_text = free_text_columns()
    out: dict[str, str] = {}
    for column in columns:
        reason = forbidden_reason(column, "masked")
        if reason is None and column in free_text:
            reason = "free_text"
        if reason is None and column not in allowed and column not in excluded:
            reason = "not allow-listed"
        if reason is not None:
            out[column] = reason
    return out


def _refuse(problems: Mapping[str, Mapping[str, str]]) -> None:
    found = [f"{t}.{c} ({why})" for t, cols in problems.items() for c, why in cols.items()]
    if found:
        raise DisallowedColumnError(
            "refusing to build the dashboard: disallowed columns " + ", ".join(found)
        )


def _read_tables(root: DataRoot) -> tuple[MonitoringData, dict[str, int]]:
    raw: dict[str, bytes] = {}
    problems: dict[str, dict[str, str]] = {}
    for name in SOURCE_TABLES:
        spec = TABLES[name]
        path = root.area("reconciled") / spec.filename
        if not path.is_file():
            raise MonitoringInputError(
                f"reconciled/{spec.filename} is missing: run av-analysis derive (or refresh)"
            )
        data = read_bytes(path)
        try:
            header, _ = parse_csv(data)
        except CsvFormatError as exc:
            raise MonitoringInputError(f"reconciled/{spec.filename}: {exc}") from exc
        bad = disallowed_columns(name, header)
        if bad:
            problems[name] = bad
        raw[name] = data
    _refuse(problems)
    keep = allowlist()
    tables: dict[str, tuple[Row, ...]] = {}
    inputs: dict[str, str] = {}
    sizes: dict[str, int] = {}
    for name, data in raw.items():
        spec = TABLES[name]
        try:
            rows = parse_table(spec, data, data_kind=root.data_kind)
        except TableFormatError as exc:
            raise MonitoringInputError(f"reconciled/{spec.filename}: {exc}") from exc
        tables[name] = tuple({c: r[c] for c in spec.header if c in keep[name]} for r in rows)
        rel = f"reconciled/{spec.filename}"
        inputs[rel] = sha256_bytes(data)
        sizes[rel] = len(data)
    return MonitoringData(root.data_kind, tables, inputs), sizes


def load_monitoring_data(root: DataRoot) -> MonitoringData:
    """Read the source tables, keep allow-listed columns, refuse anything else.

    Raises :class:`DisallowedColumnError` (a disallowed column in any header; checked for
    all three tables before any row is parsed) or :class:`MonitoringInputError` (a missing
    table, or one that does not follow ``derived.TABLES`` or the root's data kind).
    """
    return _read_tables(root)[0]


def check_monitoring_data(data: MonitoringData) -> None:
    """Raise :class:`DisallowedColumnError` unless ``data`` holds exactly the source tables,
    rows with allow-listed keys only, and the declared data kind."""
    if data.data_kind not in DATA_KINDS:
        raise DisallowedColumnError(f"unknown data kind {data.data_kind!r}")
    if set(data.tables) != set(SOURCE_TABLES):
        raise DisallowedColumnError(
            f"the dashboard reads exactly {', '.join(SOURCE_TABLES)}; got "
            f"{', '.join(sorted(data.tables))}"
        )
    keep = allowlist()
    problems: dict[str, dict[str, str]] = {}
    for name, rows in data.tables.items():
        for row in rows:
            for column in row:
                reason = forbidden_reason(column, "masked")
                if reason is None and column not in keep[name]:
                    reason = "not allow-listed"
                if reason is not None:
                    problems.setdefault(name, {})[column] = reason
            if row.get("data_kind") != data.data_kind:
                raise DisallowedColumnError(f"{name}: a row is not {data.data_kind}")
    _refuse(problems)


@cache
def _validator() -> Draft202012Validator:
    return Draft202012Validator(dashboard_data_schema())


def dashboard_document(data: MonitoringData) -> dict[str, Any]:
    """The validated dashboard-data document (``dashboard-data.schema.json``)."""
    check_monitoring_data(data)
    doc = build_document(data.tables, data.data_kind, data.inputs, version=__version__)
    error = best_match(_validator().iter_errors(doc))
    if error is not None:  # pragma: no cover - a bug in monitoring_metrics, not an input
        where = "/".join(str(p) for p in error.absolute_path)
        raise RuntimeError(f"dashboard document invalid at {where}: {error.message}")
    leaked = forbidden_keys(doc, "masked")
    if leaked:  # pragma: no cover - the schema admits no such key
        raise DisallowedColumnError(f"dashboard document has masked keys: {sorted(leaked)}")
    return doc


def render(data: MonitoringData) -> str:
    """The dashboard HTML (watermarked, see ``paths.check_watermark``)."""
    return render_document(dashboard_document(data), [(p.id, p.title) for p in PANELS])


def _build(root: DataRoot) -> tuple[Path, dict[str, Any]]:
    data, sizes = _read_tables(root)
    doc = dashboard_document(data)
    page = render_document(doc, [(p.id, p.title) for p in PANELS]).encode("utf-8")
    payload = json_bytes(doc)
    kind: DataKind = root.data_kind
    html_path = write_output(root, "monitoring", "index.html", page, kind)
    write_output(root, "monitoring", "dashboard.json", payload, kind)
    manifest = {
        "format": OUTPUTS_MANIFEST_FORMAT,
        "format_version": OUTPUTS_MANIFEST_FORMAT_VERSION,
        "data_kind": kind,
        "area": "monitoring",
        "analyzer": {"name": "av-analysis", "version": __version__},
        "seeds": [],
        "inputs": [{"path": p, "bytes": sizes[p], "sha256": data.inputs[p]} for p in sorted(sizes)],
        "files": [
            {"path": name, "bytes": len(content), "sha256": sha256_bytes(content)}
            for name, content in sorted({"dashboard.json": payload, "index.html": page}.items())
        ],
    }
    write_output(root, "monitoring", OUTPUTS_MANIFEST, json_bytes(manifest), kind)
    return html_path, doc


def write_dashboard(root: DataRoot) -> Path:
    """Regenerate ``monitoring/index.html`` (and ``dashboard.json``, ``manifest.json``).

    Nothing is written when an input is refused.
    """
    return _build(root)[0]


def dashboard_schema() -> dict[str, Any]:
    """``dashboard-data.schema.json`` (published through :data:`SCHEMAS`)."""
    return dashboard_data_schema()


SCHEMAS: Final = {SCHEMA_NAME: dashboard_schema}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Arguments of ``av-analysis dashboard``."""
    parser.add_argument("--root", required=True, help="data root (av-data-root.json)")


def main(args: argparse.Namespace) -> int:
    """Run ``av-analysis dashboard``: 0 written, 1 written with red alerts, 2 refused."""
    try:
        root = DataRoot.open(Path(args.root))
        path, doc = _build(root)
    except (DisallowedColumnError, MonitoringInputError, WatermarkError) as exc:
        print(f"av-analysis dashboard: refusing: {exc}", file=sys.stderr)
        return 2
    as_of = doc["as_of"]["data_date"] or "no data yet"
    print(f"wrote {path} ({doc['data_kind']}; data as of {as_of})")
    for alert in doc["alerts"]["suspension"]:
        print(f"RED ALERT {alert['event']} ({alert['title']}): {', '.join(alert['visit_ids'])}")
    for trigger in doc["alerts"]["triggers"]:
        scope = f" (Study {trigger['study']} {trigger['set']})" if trigger["study"] else ""
        print(f"trigger {trigger['trigger']}{scope}: {trigger['message']}")
    return 1 if doc["alerts"]["suspension"] else 0
