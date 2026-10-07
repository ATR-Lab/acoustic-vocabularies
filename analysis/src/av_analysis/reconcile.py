"""Per-visit reconciliation, checks C1-C8 (#33).

``av-analysis reconcile <visit_id>... --root DIR`` (or ``--all``) loads a raw visit
(``loaders``) and its reference inputs (``references``), runs the checks of
``codes.CHECKS`` (rules in ``reconcile_checks``) and writes
``reconciled/<visit_id>/reconciliation.json`` (``reconciliation.schema.json``) through
``paths.write_output``. Rules:

* Raw files are opened read-only; the SHA-256 values of every raw file the run reads (the
  visit's folder, the person's earlier visits, the other dyad member's visit for C6 and
  ``raw/deviations-log.csv``) are taken before and after the run and must be identical
  (``raw_unchanged``; acceptance criterion). Otherwise C1 reports ``RAW_HASH_CHANGED``
  naming the files (data-root paths), which no deviation record can resolve, and the
  visit fails (``Report.passed``; exit 1).
* Deterministic: no run time, inputs listed sorted by path, discrepancies in check order
  then by rows; the same inputs give identical bytes.
* Each discrepancy has a code (``codes.CODES``), the rows involved and a deviation link
  (trial-log ``deviation_id``, exposure-ledger ``matching_deviation_id``, or a deviations
  record whose ``event_id`` names the row, visit or person, with a category that fits the
  code: ``reconcile_checks.LINK_CATEGORIES``); otherwise it is unresolved and C8 reports
  ``DEVIATION_MISSING``. Records are the visit's ``deviations.csv`` and only those
  ``raw/deviations-log.csv`` records that concern the visit
  (``reconcile_checks.visit_records``: the log names rows as ``<visit_id>/<row>``, so a
  record about another visit, person or unit never explains this visit's discrepancies).
  Check status: ``pass`` (none), ``explained`` (all linked),
  ``fail`` (any unresolved), ``not_applicable`` (study or visit without it, or, for C2-C6,
  reference inputs that could not be loaded: C1 then fails the visit with
  ``REFERENCE_INPUT``).
* Never computes accuracy by condition, never writes outcome, response, response-time,
  rating or condition fields (``masking`` policy ``masked``).
* **C6 is symmetric.** Study B C6 (yoked ledger) is evaluated once per dyad and visit
  over both members' exposure ledgers and run sheets, and the same C6 status,
  discrepancies (same codes, rows and details) and partner input files are written into
  both members' reports, as ``pair_gap_hours`` is reported on both members' rows. C6
  ``rows`` list event IDs sorted, never marked as source or copy, and details name no
  role, so no report, visit-status row or discrepancy row shows which member was yoked.
  For V1-V3 C6 is never ``not_applicable`` for one member only: while the other member's
  visit has no raw logs, C6 reports ``YOKED_SOURCE_MISSING`` naming that visit.
* **Lost opportunities.** A scheduled opportunity without a trial-log row is a
  ``COUNT_MISSING_TRIAL`` discrepancy. When a deviation record verifies an apparatus or
  logger failure for it (category ``technical`` or ``audio``, ``event_id`` naming the
  trial, the visit or the session) the discrepancy is resolved and ``derive`` emits a
  ``trials`` row with ``row_source`` deviation (operational score 0). When the record is
  a withdrawal (or a comfort stop), the opportunity was never undertaken and gets no row.
* Raw times (run sheet, deviations) are ISO 8601 with a UTC offset
  (``vocab.parse_timestamp``); a naive time is a ``RAW_FORMAT`` discrepancy.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .codes import CHECK_BY_ID, SUSPENSION_TITLES, CheckId, suspension_event
from .derived import TOKEN_RE
from .fileio import json_bytes, read_bytes, sha256_bytes
from .ledger import VisitLogs, visit_logs
from .loaders import RawVisit, RefusedInputError, load_deviations_log, load_raw_visit, raw_visit_ids
from .paths import (
    DEVIATIONS_LOG,
    RECONCILIATION_REPORT,
    DataRoot,
    WatermarkError,
    parse_visit_id,
    write_output,
)
from .reconcile_checks import (
    Context,
    Found,
    Linked,
    Record,
    c6_applies,
    collect,
    evaluate,
    finish,
    records_of,
    scope_of,
    visit_records,
)
from .references import (
    InputReader,
    ReferenceError,
    References,
    load_references_with,
    set_of_unit,
)
from .schemas import RECONCILIATION_FORMAT, RECONCILIATION_FORMAT_VERSION, validator
from .vocab import VISITS, CheckStatus, DataKind

_TOKEN = re.compile(TOKEN_RE)


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

    @property
    def visit_id(self) -> str:
        return f"{self.person_id}-{self.visit}"

    def counted(self) -> list[Discrepancy]:
        """Discrepancies of C1-C7 plus DEVIATION_UNKNOWN (``summary.discrepancies``)."""
        return [
            d
            for c in self.checks
            for d in c.discrepancies
            if c.check != "C8" or d.code == "DEVIATION_UNKNOWN"
        ]

    @property
    def passed(self) -> bool:
        """No check fails and no raw file changed during the run."""
        return self.raw_unchanged and all(c.status != "fail" for c in self.checks)

    def document(self) -> dict[str, Any]:
        """The JSON document of the report."""
        discrepancies: list[dict[str, Any]] = []
        for c in self.checks:
            for d in c.discrepancies:
                discrepancies.append(
                    {
                        "seq": len(discrepancies) + 1,
                        "check": d.check,
                        "code": d.code,
                        "rows": list(d.rows),
                        "deviation_id": d.deviation_id,
                        "resolved": d.resolved,
                        "suspension_event": suspension_event(d.code),
                        "detail": d.detail,
                    }
                )
        counted = self.counted()
        events = {suspension_event(d.code) for d in counted}
        return {
            "format": RECONCILIATION_FORMAT,
            "format_version": RECONCILIATION_FORMAT_VERSION,
            "data_kind": self.data_kind,
            "analyzer": {"name": "av-analysis", "version": __version__},
            "study": self.study,
            "set": self.set_name,
            "unit_id": self.unit_id,
            "person_id": self.person_id,
            "visit": self.visit,
            "visit_id": self.visit_id,
            "session_id": self.session_id,
            "station_id": self.station_id,
            "inputs": [{"path": p, "bytes": n, "sha256": s} for p, n, s in self.inputs],
            "raw_unchanged": self.raw_unchanged,
            "checks": [
                {
                    "check": c.check,
                    "name": CHECK_BY_ID[c.check].name,
                    "status": c.status,
                    "discrepancies": len(c.discrepancies),
                    "unresolved": sum(not d.resolved for d in c.discrepancies),
                }
                for c in self.checks
            ],
            "discrepancies": discrepancies,
            "summary": {
                "status": "pass" if self.passed else "fail",
                "discrepancies": len(counted),
                "unresolved": sum(not d.resolved for d in counted),
                "suspension_events": [e for e in SUSPENSION_TITLES if e in events],
            },
        }


# ---------------------------------------------------------------------------------------
# Context


def partner_slot(person: str) -> str:
    """The other member slot of a Study B dyad slot (``B-C01-M1`` <-> ``B-C01-M2``)."""
    return person[:-1] + ("2" if person.endswith("1") else "1")


def related_visits(visit_id: str) -> tuple[list[str], str | None]:
    """(earlier visits of the person, the dyad partner's visit for C6 or None)."""
    person, visit = parse_visit_id(visit_id)
    order = VISITS[person[0]]  # type: ignore[index]
    earlier = [f"{person}-{v}" for v in order[: order.index(visit)]]
    partner = f"{partner_slot(person)}-{visit}" if c6_applies(person[0], visit) else None
    return earlier, partner


class _Cache:
    """Raw visits loaded during one run (a raw file never changes during a run)."""

    def __init__(self, root: DataRoot) -> None:
        self.root = root
        self.visits: dict[str, VisitLogs | None] = {}

    def logs(self, visit_id: str) -> VisitLogs | None:
        if visit_id not in self.visits:
            try:
                self.visits[visit_id] = visit_logs(load_raw_visit(self.root, visit_id))
            except FileNotFoundError:
                self.visits[visit_id] = None
        return self.visits[visit_id]


def _raw_hashes(root: DataRoot, visit_ids: Sequence[str]) -> dict[str, str]:
    """SHA-256 of every raw file of the folders and of the study-wide log."""
    out: dict[str, str] = {}
    for vid in visit_ids:
        folder = root.raw_visit_dir(vid)
        if folder.is_dir():
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    out[path.relative_to(root.path).as_posix()] = sha256_bytes(read_bytes(path))
    log = root.input_path("raw", DEVIATIONS_LOG)
    if log.is_file():
        out[f"raw/{DEVIATIONS_LOG}"] = sha256_bytes(read_bytes(log))
    return out


def build_context(
    root: DataRoot,
    raw: RawVisit,
    refs: References | None,
    reference_error: tuple[str, str] | None = None,
    cache: _Cache | None = None,
) -> Context:
    """The :class:`~av_analysis.reconcile_checks.Context` of one visit."""
    cache = cache or _Cache(root)
    this = visit_logs(raw)
    cache.visits.setdefault(raw.visit_id, this)
    earlier, partner_id = related_visits(raw.visit_id)
    history: dict[str, VisitLogs] = {}
    for vid in earlier:
        logs = cache.logs(vid)
        if logs is not None:
            history[logs.visit] = logs
    partner = cache.logs(partner_id) if partner_id is not None else None
    log = load_deviations_log(root)
    log_records = records_of(log)
    scope = scope_of(this, raw.visit_id, [refs.participant_id if refs else _coded(this)])
    partner_records: list[Record] = []
    if partner_id is not None:
        partner_scope = scope_of(partner, partner_id, [_coded(partner)])
        own = partner.raw.deviations if partner is not None else None
        partner_records = visit_records(own, log_records, partner_scope)
    return Context(
        root_synthetic=root.synthetic,
        this=this,
        refs=refs,
        reference_error=reference_error,
        history=history,
        partner=partner,
        partner_id=partner_id,
        log=log,
        scope=scope,
        records=visit_records(raw.deviations, log_records, scope),
        partner_records=partner_records,
    )


def _coded(logs: VisitLogs | None) -> str | None:
    """The coded participant ID a visit's trial log records (first row), if any."""
    if logs is None or not logs.trials:
        return None
    return logs.trials[0].get("participant_id") or None


def _results(linked: list[tuple[str, CheckStatus, list[Linked]]]) -> tuple[CheckResult, ...]:
    out = []
    for check, status, items in linked:
        out.append(
            CheckResult(
                check=check,  # type: ignore[arg-type]
                status=status,
                discrepancies=tuple(
                    Discrepancy(
                        check=d.found.check,
                        code=d.found.code,
                        rows=d.found.rows,
                        deviation_id=d.deviation_id,
                        resolved=d.resolved,
                        detail=d.found.detail,
                    )
                    for d in items
                ),
            )
        )
    return tuple(out)


def run_checks(raw: RawVisit, refs: References, root: DataRoot) -> tuple[CheckResult, ...]:
    """Checks C1-C8 of one visit, in order (C8 last, over the other seven)."""
    return _results(evaluate(build_context(root, raw, refs)))


def _inputs(ctx: Context, reader: InputReader, log_size: int) -> tuple[tuple[str, int, str], ...]:
    """Every file the run read: raw folders, the study-wide log and reference inputs."""
    entries: dict[str, tuple[int, str]] = {}
    visits = [ctx.this, *ctx.history.values(), *([ctx.partner] if ctx.partner else [])]
    for logs in visits:
        for name, sha in logs.raw.files.items():
            entries[logs.raw.rel(name)] = (logs.raw.sizes[name], sha)
    if ctx.log is not None:
        entries[ctx.log.path] = (log_size, ctx.log.sha256)
    for rel, sha in reader.sha.items():
        entries[rel] = (reader.size[rel], sha)
    return tuple((rel, size, sha) for rel, (size, sha) in sorted(entries.items()))


def _token(value: object) -> str | None:
    return value if isinstance(value, str) and _TOKEN.fullmatch(value) else None


def reconcile_visit(root: DataRoot, visit_id: str, *, cache: _Cache | None = None) -> Report:
    """Reconcile one visit (raw files read-only, hashed before and after).

    Raises ``ValueError`` when the visit has no raw folder and
    :class:`~av_analysis.loaders.RefusedInputError` for inputs of the other data kind.
    """
    person, visit = parse_visit_id(visit_id)
    earlier, partner_id = related_visits(visit_id)
    scope = [visit_id, *earlier, *([partner_id] if partner_id else [])]
    before = _raw_hashes(root, scope)
    try:
        raw = load_raw_visit(root, visit_id)
    except FileNotFoundError:
        raise ValueError(f"{visit_id}: no raw folder (visit not held or not imported)") from None
    reader = InputReader(root)
    refs: References | None = None
    error: tuple[str, str] | None = None
    try:
        refs = load_references_with(root, visit_id, reader)
    except ReferenceError as exc:
        error = (exc.path, exc.message)
    ctx = build_context(root, raw, refs, error, cache)
    collected = collect(ctx)
    log_path = root.input_path("raw", DEVIATIONS_LOG)
    log_size = len(read_bytes(log_path)) if ctx.log is not None else 0
    inputs = _inputs(ctx, reader, log_size)
    after = _raw_hashes(root, scope)
    changed = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
    extra: list[Found] = []
    if changed:  # never explained by a deviation record: rerun on stable raw files
        detail = "raw file changed, appeared or disappeared while reconciliation ran"
        extra.append(Found("C1", "RAW_HASH_CHANGED", tuple(changed), detail, linkable=False))
    checks = _results(finish(ctx, collected, extra))
    manifest = raw.exit_manifest or {}
    unit = person[:5]
    return Report(
        data_kind=root.data_kind,
        study=person[0],
        set_name=set_of_unit(unit),
        unit_id=unit,
        person_id=person,
        visit=visit,
        session_id=_token(manifest.get("session_id")),
        station_id=_token(manifest.get("station_id")),
        inputs=inputs,
        raw_unchanged=not changed,
        checks=checks,
    )


def report_bytes(report: Report) -> bytes:
    """Canonical bytes of a report, validated against ``reconciliation.schema.json``."""
    doc = report.document()
    errors = list(validator("reconciliation.schema.json").iter_errors(doc))
    if errors:  # pragma: no cover - a defect of this module
        raise AssertionError(f"report does not match its schema: {errors[0].message}")
    return json_bytes(doc)


def write_report(root: DataRoot, report: Report) -> Path:
    """Write ``reconciled/<visit_id>/reconciliation.json``."""
    path = f"{report.visit_id}/{RECONCILIATION_REPORT}"
    return write_output(root, "reconciled", path, report_bytes(report), root.data_kind)


def reconcile_many(root: DataRoot, visit_ids: Sequence[str]) -> list[Report]:
    """Reconcile several visits in order (one report each)."""
    cache = _Cache(root)
    return [reconcile_visit(root, v, cache=cache) for v in visit_ids]


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Arguments of ``av-analysis reconcile``."""
    parser.add_argument("visit_ids", nargs="*", help="visit IDs such as A-C07-L03-D0")
    parser.add_argument("--root", required=True, help="data root (av-data-root.json)")
    parser.add_argument("--all", action="store_true", help="every visit folder under raw/")


def main(args: argparse.Namespace) -> int:
    """Run ``av-analysis reconcile``: exit 0 when every visit passes, 1 otherwise, 2 for a
    refused input (not a data root, unknown visit, data of the other kind)."""
    try:
        root = DataRoot.open(Path(args.root))
        ids = raw_visit_ids(root) if args.all else list(args.visit_ids)
        for vid in ids:
            parse_visit_id(vid)
    except (WatermarkError, ValueError, OSError) as exc:
        print(f"reconcile: refusing: {exc}", file=sys.stderr)
        return 2
    if not ids:
        print("reconcile: no visits (give visit IDs or --all)", file=sys.stderr)
        return 0 if args.all else 2
    cache = _Cache(root)
    worst = 0
    for vid in ids:
        try:
            report = reconcile_visit(root, vid, cache=cache)
            write_report(root, report)
        except (RefusedInputError, WatermarkError, ValueError) as exc:
            print(f"reconcile: refusing {vid}: {exc}", file=sys.stderr)
            return 2
        doc = report.document()["summary"]
        failed = [c.check for c in report.checks if c.status == "fail"]
        print(
            f"{vid}: {doc['status']} ({doc['discrepancies']} discrepancies, "
            f"{doc['unresolved']} unresolved{', failed ' + ' '.join(failed) if failed else ''})"
        )
        worst = max(worst, 0 if report.passed else 1)
    return worst
