"""Verify every bank, compile the register and archive, and check the register commit (#28).

After the run:

1. `verify_all`: `banks verify` (`av_banks.verify.verify_bank`: re-render, hashes,
   provenance, caps and all 1,920 different-atom pairs per profile) on every complete and
   unavailable bank; one JSON report per bank in `verify/`, `verification-log.txt` (one
   line per bank and one per problem) and `verification.json`.
2. `compile_register` (refused while a bank is pending, running or crashed, or a report
   is missing or stale):
   - `used-seeds.json`: the seeds the slot records used (`seed_check.check_used_seeds`);
   - `timing.csv` (one row per bank version and an `ALL` row: wall time, slots per
     minute, whether the wall time is estimated, model latency and slot time
     p50/p95/max, slots over the 40-s cap, failed model calls) and `slot-timing.csv` (one
     row per slot): the run timing log. A bank row adds the time of an unfinished
     (crashed) attempt from its timing events and slot records; the `ALL` row is the
     runner time, and a killed runner's session ends at its last record. Both are then
     flagged `wall_estimated`;
   - `register.csv` (`REGISTER_COLUMNS`, one row per bank in dyad-slot sequence): bank
     ID, role, dyad slot, version, status, whether it may be assigned, config hash and
     its match with the freeze value, attempts, attempt used, slots, verify result and
     report hash, bank SHA-256;
   - the archive `archive/<campaign>-banks.tar` (`write_archive`: every campaign file
     except the archive, `register.json`, `g5b-report.md` and the lock, in sorted POSIX
     order with fixed metadata, so the same files always give the same SHA-256);
   - `register.json` (`banks/schema/confirmatory-register.schema.json`): counts, checks,
     the decision and escalation, the unavailable bank IDs, the freeze reference (with
     whether the freeze tag and #25's freeze guard were checked), and the hashes of the
     plan, seed check, register CSV, verification and timing logs and the archive;
   - `g5b-report.md`: the counts and decision for the G5B owner.
3. **Counts and escalation.** Spares do not change the count: at least 64 of the 72
   banks must be complete. If fewer are, the decision is `escalation_required`: stop and
   escalate to the advisor before G5B, then record it (`record_escalation`: role, link,
   date; never a name) and compile again (`escalated`). Unavailable banks are never
   assignable; the coordinator logs each with the allocation reveal API
   (`log_bank_unavailable`, schedules #31) before the first reveal, which replaces an
   unavailable main slot by the first unused spare of the same SQ arm and swap flag.
4. **Timestamped commit.** `publish_register` copies `register.csv` and `register.json`
   (hashes and counts only) into the repository; after the commit,
   `check_register_commit` confirms that the committed files equal the campaign's, that
   they were added once and never changed, and that the commit time precedes the first
   confirmatory screening (`ok` is false when either the file hash or the screening time
   is not given, so nothing passes unchecked).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal

from av_generation.clock import Clock, utc_text
from av_generation.constants import B_MAX_ATTEMPTS, B_SLOTS_PER_ATTEMPT, SLOT_CAP_MS
from av_generation.jsonio import file_sha256, iter_jsonl, read_json, to_json_value, write_document

from av_banks.layout import BankLayout
from av_banks.manifest import AttemptSummary, BankManifest
from av_banks.throughput import nearest_rank
from av_banks.verify import VerifyReport, verify_bank

from .common import (
    E_COMMIT,
    E_INPUT,
    E_STATE,
    E_VERIFY,
    MIN_COMPLETE,
    N_BANKS,
    CampaignError,
    CampaignLayout,
    require_schema,
)
from .plan import CampaignPlan, PlannedBank, append_event, bank_history, read_plan, read_rebuilds
from .runner import bank_progress
from .seed_check import SeedEntry, UsedSeeds, check_used_seeds

REGISTER_FORMAT: Final = "av-banks/confirmatory-register"
REGISTER_VERSION: Final = 1
REGISTER_SCHEMA: Final = "confirmatory-register.schema.json"
ESCALATION_FORMAT: Final = "av-banks/confirmatory-escalation"
REGISTER_COLUMNS: Final[tuple[str, ...]] = (
    "sequence",
    "bank_id",
    "role",
    "dyad_slot",
    "bank_version",
    "status",
    "assignable",
    "generation_config_sha256",
    "config_matches_freeze",
    "attempts",
    "attempt_used",
    "slots_attempt_used",
    "max_slots_per_attempt",
    "slots_total",
    "crashed_versions",
    "crashed_slots",
    "verify",
    "verify_report_sha256",
    "bank_sha256",
)
TIMING_COLUMNS: Final[tuple[str, ...]] = (
    "bank_id",
    "bank_version",
    "status",
    "attempts",
    "slots",
    "wall_ms",
    "slots_per_minute",
    "wall_estimated",
    "latency_ms_p50",
    "latency_ms_p95",
    "latency_ms_max",
    "slot_ms_p50",
    "slot_ms_p95",
    "slot_ms_max",
    "slots_over_cap",
    "llm_server_errors",
    "llm_timeouts",
    "started_utc",
    "ended_utc",
)
SLOT_TIMING_COLUMNS: Final[tuple[str, ...]] = (
    "bank_id",
    "bank_version",
    "attempt",
    "slot_id",
    "outcome",
    "llm_status",
    "latency_ms",
    "slot_ms",
)
SPARE_RULE: Final = (
    "Spares do not change the count: at least 64 of the 72 banks must be complete, else "
    "stop and escalate to the advisor before G5B. An unavailable bank is never assigned; "
    "log each with the allocation reveal API before the first reveal, which replaces an "
    "unavailable main slot, at its position, by the first unused spare with the same SQ "
    "arm and swap flag (schedules #31)."
)
ESCALATION_RULE: Final = "complete banks < 64: stop and escalate to the advisor before G5B"
RUNNER_ACTIVITY: Final = frozenset(
    {
        "runner_start",
        "log_repaired",
        "bank_crashed",
        "bank_start",
        "bank_end",
        "bank_error",
        "bank_not_started",
        "runner_end",
    }
)
"""`events.jsonl` events a runner writes during its session."""
SESSION_BOUNDARIES: Final = frozenset({"runner_start", "lock_broken"})
"""Events that begin a new runner session (and so end a killed runner's session)."""
REFERENCE_RE: Final = re.compile(
    r"https://github\.com/ATR-Lab/acoustic-vocabularies/(issues|pull)/[0-9]+(#[!-~]+)?"
)
DATE_RE: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
REPO_URL: Final = "https://github.com/ATR-Lab/acoustic-vocabularies"
ARCHIVE_EXCLUDE: Final = frozenset({"register.json", "g5b-report.md", "runner.lock"})

Decision = Literal["ready", "escalation_required", "escalated", "blocked"]


def _write_csv(path: Path, columns: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if row.get(c) is None else row.get(c) for c in columns])
    text = buffer.getvalue()
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _versions(
    root: str | os.PathLike[str],
) -> tuple[CampaignPlan, dict[str, tuple[PlannedBank, ...]]]:
    plan = read_plan(root)
    return plan, bank_history(plan, read_rebuilds(root))


# ---------------------------------------------------------------------------
# verify-all


@dataclass(frozen=True, slots=True)
class VerifyAll:
    verified: int
    ok: int
    failed: tuple[str, ...]
    log_sha256: str
    reports: Mapping[str, str]
    """`<bank_id>` -> SHA-256 of its report file (current versions with a report)."""


def _log_line(bank: PlannedBank, report: Mapping[str, Any]) -> list[str]:
    pairs = report.get("pairs_checked") or {}
    pair_text = ",".join(f"{k}:{pairs[k]}" for k in sorted(pairs)) or "-"
    head = (
        f"{bank.bank_id} v{bank.bank_version} {report.get('status')} "
        f"{'OK' if report.get('ok') else 'FAILED'} bank={report.get('bank_sha256')} "
        f"attempts={report.get('attempts_checked')} slots={report.get('slots_checked')} "
        f"options={report.get('options_checked')} pairs={pair_text} "
        f"problems={len(report.get('problems') or ())}"
    )
    return [head, *(f"  PROBLEM: {p}" for p in report.get("problems") or ())]


def verify_all(
    root: str | os.PathLike[str], *, jobs: int = 1, only: Sequence[str] | None = None
) -> VerifyAll:
    """Verify the complete and unavailable banks (all, or `only`), then rewrite the
    verification log and summary from every current report."""
    layout = CampaignLayout.at(root)
    plan, history = _versions(root)
    current = [history[b.bank_id][-1] for b in plan.banks]
    done = [
        b
        for b in current
        if bank_progress(layout, b, active=False).state in ("complete", "unavailable")
        and (only is None or b.bank_id in only)
    ]
    layout.verify_dir.mkdir(parents=True, exist_ok=True)

    def one(bank: PlannedBank) -> VerifyReport:
        bank_dir = layout.bank_dir(bank.run_id, bank.bank_id)
        report = verify_bank(bank_dir)
        data = report.to_dict()
        # the manifest file the report is about (a later change makes the report stale)
        data["manifest_sha256"] = file_sha256(BankLayout(bank_dir).manifest)
        write_document(layout.verify_report(bank.bank_id, bank.bank_version), data)
        return report

    if jobs <= 1:
        for bank in done:
            one(bank)
    else:
        with ThreadPoolExecutor(jobs, thread_name_prefix="verify") as pool:
            list(pool.map(one, done))
    lines = [f"# banks verify: campaign {plan.campaign_id} ({plan.set})"]
    reports: dict[str, str] = {}
    failed: list[str] = []
    verified = 0
    for bank in current:
        path = layout.verify_report(bank.bank_id, bank.bank_version)
        if not path.is_file():
            lines.append(f"{bank.bank_id} v{bank.bank_version} NOT VERIFIED")
            continue
        report = read_json(path)
        verified += 1
        reports[bank.bank_id] = file_sha256(path)
        lines.extend(_log_line(bank, report))
        if not report.get("ok"):
            failed.append(bank.bank_id)
    lines.append(
        f"# {verified} of {len(current)} banks verified; {verified - len(failed)} OK; "
        f"{len(failed)} failed{': ' + ', '.join(failed) if failed else ''}"
    )
    text = "\n".join(lines) + "\n"
    with open(layout.verification_log, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    log_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    write_document(
        layout.verification,
        {
            "campaign_id": plan.campaign_id,
            "verified": verified,
            "ok": verified - len(failed),
            "failed": failed,
            "log_sha256": log_sha,
            "reports": reports,
        },
    )
    return VerifyAll(verified, verified - len(failed), tuple(failed), log_sha, reports)


# ---------------------------------------------------------------------------
# Timing log


def _slot_records(bank_dir: Path) -> list[tuple[int, dict[str, Any]]]:
    layout = BankLayout(bank_dir)
    out: list[tuple[int, dict[str, Any]]] = []
    for attempt in layout.attempts():
        path = layout.slots(attempt)
        if path.is_file():
            out.extend((attempt, r) for r in iter_jsonl(path) if isinstance(r, dict))
    return out


def _quantiles(prefix: str, values: Sequence[int]) -> dict[str, int | None]:
    return {
        f"{prefix}_p50": nearest_rank(values, 0.5),
        f"{prefix}_p95": nearest_rank(values, 0.95),
        f"{prefix}_max": max(values) if values else None,
    }


def _jsonl(path: Path) -> list[Any]:
    """The complete lines of a JSONL log; a line a killed process left unfinished at the
    end is skipped (the runner cuts it, logged, when it next starts)."""
    if not path.is_file():
        return []
    data = path.read_bytes()
    return [json.loads(raw) for raw in data[: data.rfind(b"\n") + 1].splitlines() if raw]


def _moment(text: object) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


@dataclass(frozen=True, slots=True)
class AttemptTime:
    """Wall time of one attempt on disk."""

    attempt: int
    wall_ms: int
    started_utc: str | None
    ended_utc: str | None
    estimated: bool
    """`True` for an unfinished attempt (no `attempt.json`: its build crashed)."""


def attempt_times(
    bank_dir: Path, records: Sequence[tuple[int, Mapping[str, Any]]]
) -> list[AttemptTime]:
    """Wall time of every attempt of a bank directory: from `attempt.json` for a finished
    attempt; for an unfinished one, from its `attempt_start` timing event to its last
    timing event or slot record (`records`: `(attempt, slot record)`), estimated."""
    blayout = BankLayout(bank_dir)
    events = [e for e in _jsonl(blayout.timing) if isinstance(e, dict)]
    out: list[AttemptTime] = []
    for attempt in blayout.attempts():
        path = blayout.attempt_summary(attempt)
        if path.is_file():
            summary = AttemptSummary.read(path)
            out.append(
                AttemptTime(attempt, summary.wall_ms, summary.started_utc, summary.ended_utc, False)
            )
            continue
        mine = [e for e in events if e.get("attempt") == attempt and isinstance(e.get("t_ms"), int)]
        start = next((e for e in mine if e.get("event") == "attempt_start"), None)
        if start is None:
            out.append(AttemptTime(attempt, 0, None, None, True))
            continue
        t0 = int(start["t_ms"])
        t1 = max(
            [
                t0,
                *(int(e["t_ms"]) for e in mine),
                *(
                    int(r["t_ms"])
                    for a, r in records
                    if a == attempt and isinstance(r.get("t_ms"), int)
                ),
            ]
        )
        began = _moment(start.get("wall_utc"))
        out.append(
            AttemptTime(
                attempt,
                t1 - t0,
                None if began is None else utc_text(began),
                None if began is None else utc_text(began + timedelta(milliseconds=t1 - t0)),
                True,
            )
        )
    return out


def _timing_row(
    bank_id: str,
    version: str,
    status: str,
    records: Sequence[Mapping[str, Any]],
    times: Sequence[AttemptTime],
) -> dict[str, Any]:
    latency = [int(r["latency_ms"]) for r in records if isinstance(r.get("latency_ms"), int)]
    slot_ms = [int(r["t_ms"]) - int(r["t_open_ms"]) for r in records]
    wall = sum(t.wall_ms for t in times)
    return {
        "bank_id": bank_id,
        "bank_version": version,
        "status": status,
        "attempts": len(times),
        "slots": len(records),
        "wall_ms": wall,
        "slots_per_minute": round(len(records) * 60_000 / wall, 3) if wall > 0 else None,
        "wall_estimated": int(any(t.estimated for t in times)),
        **_quantiles("latency_ms", latency),
        **_quantiles("slot_ms", slot_ms),
        "slots_over_cap": sum(1 for ms in slot_ms if ms > SLOT_CAP_MS),
        "llm_server_errors": sum(1 for r in records if r.get("llm_status") == "server_error"),
        "llm_timeouts": sum(1 for r in records if r.get("llm_status") == "timeout"),
        "started_utc": min((t.started_utc for t in times if t.started_utc), default=None),
        "ended_utc": max((t.ended_utc for t in times if t.ended_utc), default=None),
    }


@dataclass(frozen=True, slots=True)
class RunnerTime:
    """Runner sessions of a campaign (`runner_time`)."""

    wall_ms: int
    sessions: int
    unclosed: int
    """Sessions without `runner_end` (a killed runner), ended at their last record."""


def runner_time(layout: CampaignLayout, activity: Iterable[str] = ()) -> RunnerTime:
    """Total runner time from `events.jsonl`: every `runner_start` to its `runner_end`. A
    session without `runner_end` (the runner was killed) ends at its last record before
    the next `runner_start` or `lock_broken`: the latest runner event, progress snapshot
    or bank activity time (`activity`, UTC texts such as attempt end times)."""
    events = [
        (moment, str(e.get("event")))
        for e in _jsonl(layout.events)
        if isinstance(e, dict) and (moment := _moment(e.get("at_utc"))) is not None
    ]
    marks = [at for at, name in events if name in RUNNER_ACTIVITY]
    marks += [
        moment
        for p in _jsonl(layout.progress)
        if isinstance(p, dict) and (moment := _moment(p.get("at_utc"))) is not None
    ]
    marks += [moment for text in activity if (moment := _moment(text)) is not None]

    def last_mark(lo: datetime, hi: datetime | None) -> datetime:
        return max((m for m in marks if lo <= m and (hi is None or m < hi)), default=lo)

    total = sessions = unclosed = 0
    start: datetime | None = None
    for at, name in events:
        if name in SESSION_BOUNDARIES:
            if start is not None:
                total += int((last_mark(start, at) - start).total_seconds() * 1000)
                unclosed += 1
                start = None
            if name == "runner_start":
                start = at
                sessions += 1
        elif name == "runner_end" and start is not None:
            total += int((at - start).total_seconds() * 1000)
            start = None
    if start is not None:
        total += int((last_mark(start, None) - start).total_seconds() * 1000)
        unclosed += 1
    return RunnerTime(total, sessions, unclosed)


@dataclass(frozen=True, slots=True)
class TimingLog:
    timing_sha256: str
    slot_timing_sha256: str
    totals: Mapping[str, Any]
    runner: RunnerTime
    rows: tuple[Mapping[str, Any], ...]
    """The rows of `timing.csv` (the `ALL` row last)."""


def timing_log(root: str | os.PathLike[str]) -> TimingLog:
    """Write `timing.csv` and `slot-timing.csv` (every bank version, crashed ones too)."""
    layout = CampaignLayout.at(root)
    plan, history = _versions(root)
    rows: list[dict[str, Any]] = []
    slot_rows: list[dict[str, Any]] = []
    every: list[dict[str, Any]] = []
    activity: list[str] = []
    for bank in plan.banks:
        for version in history[bank.bank_id]:
            bank_dir = layout.bank_dir(version.run_id, version.bank_id)
            records = _slot_records(bank_dir)
            times = attempt_times(bank_dir, records)
            activity.extend(t.ended_utc for t in times if t.ended_utc)
            state = bank_progress(layout, version, active=False).state
            rows.append(
                _timing_row(
                    bank.bank_id, version.bank_version, state, [r for _, r in records], times
                )
            )
            every.extend(r for _, r in records)
            for attempt, r in records:
                slot_rows.append(
                    {
                        "bank_id": version.bank_id,
                        "bank_version": version.bank_version,
                        "attempt": attempt,
                        "slot_id": r.get("slot_id"),
                        "outcome": r.get("outcome"),
                        "llm_status": r.get("llm_status"),
                        "latency_ms": r.get("latency_ms"),
                        "slot_ms": int(r["t_ms"]) - int(r["t_open_ms"]),
                    }
                )
    total = _timing_row("ALL", "", "", every, [])
    runner = runner_time(layout, activity)
    wall = runner.wall_ms
    total.update(
        attempts=sum(int(r["attempts"]) for r in rows),
        wall_ms=wall,
        slots_per_minute=round(len(every) * 60_000 / wall, 3) if wall > 0 else None,
        wall_estimated=int(runner.unclosed > 0),
        started_utc=min((r["started_utc"] for r in rows if r["started_utc"]), default=None),
        ended_utc=max((r["ended_utc"] for r in rows if r["ended_utc"]), default=None),
    )
    rows.append(total)
    digest = _write_csv(layout.timing, TIMING_COLUMNS, rows)
    slot_digest = _write_csv(layout.slot_timing, SLOT_TIMING_COLUMNS, slot_rows)
    return TimingLog(digest, slot_digest, total, runner, tuple(rows))


# ---------------------------------------------------------------------------
# Counts and the decision


@dataclass(frozen=True, slots=True)
class Counts:
    banks: int
    complete: int
    unavailable: int
    main_complete: int
    main_unavailable: int
    spares_complete: int
    spares_unavailable: int


def tally(rows: Sequence[Mapping[str, Any]]) -> Counts:
    """Counts of register rows (`role`, `status`)."""

    def n(role: str | None, status: str) -> int:
        return sum(1 for r in rows if r["status"] == status and (role is None or r["role"] == role))

    return Counts(
        banks=len(rows),
        complete=n(None, "complete"),
        unavailable=n(None, "unavailable"),
        main_complete=n("main", "complete"),
        main_unavailable=n("main", "unavailable"),
        spares_complete=n("spare", "complete"),
        spares_unavailable=n("spare", "unavailable"),
    )


def escalation_required(counts: Counts) -> bool:
    """Fewer than 64 of the 72 banks are complete (spares do not change the count)."""
    return counts.complete < MIN_COMPLETE


def decide(counts: Counts, *, checks_ok: bool, escalated: bool) -> Decision:
    """`blocked` when a check fails; else `ready` with at least 64 complete banks;
    else `escalated` once the escalation is recorded, `escalation_required` before."""
    if not checks_ok:
        return "blocked"
    if not escalation_required(counts):
        return "ready"
    return "escalated" if escalated else "escalation_required"


# ---------------------------------------------------------------------------
# Archive


@dataclass(frozen=True, slots=True)
class ArchiveInfo:
    name: str
    sha256: str
    files: int
    bytes: int


def archive_members(root: Path) -> list[str]:
    """The campaign files the archive holds (sorted POSIX relative paths)."""
    names = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.split("/", 1)[0] == "archive" or rel in ARCHIVE_EXCLUDE:
            continue
        names.append(rel)
    return sorted(names)


def write_archive(root: str | os.PathLike[str], target: str | os.PathLike[str]) -> ArchiveInfo:
    """A deterministic POSIX tar of the campaign (`archive_members`): files only, sorted,
    mtime 0, uid/gid 0, mode 0644, no owner names. The same files give the same bytes on
    every platform."""
    base = Path(root)
    out = Path(target)
    out.parent.mkdir(parents=True, exist_ok=True)
    members = archive_members(base)
    temp = out.with_name(out.name + ".partial")
    with tarfile.open(temp, "w", format=tarfile.PAX_FORMAT) as tar:
        for name in members:
            data = (base / name).read_bytes()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    os.replace(temp, out)
    return ArchiveInfo(out.name, file_sha256(out), len(members), out.stat().st_size)


# ---------------------------------------------------------------------------
# The register


@dataclass(frozen=True, slots=True)
class RegisterResult:
    decision: Decision
    counts: Counts
    register_csv_sha256: str
    register_json_sha256: str
    archive: ArchiveInfo
    problems: tuple[str, ...]


def register_row(
    layout: CampaignLayout, plan: CampaignPlan, versions: Sequence[PlannedBank]
) -> tuple[dict[str, Any], list[str]]:
    bank = versions[-1]
    problems: list[str] = []
    bank_dir = layout.bank_dir(bank.run_id, bank.bank_id)
    manifest = BankManifest.read(BankLayout(bank_dir).manifest)
    report_path = layout.verify_report(bank.bank_id, bank.bank_version)
    if not report_path.is_file():
        raise CampaignError(E_VERIFY, f"{bank.bank_id}: no verify report (run verify-all)")
    report = read_json(report_path)
    digest = manifest.bank_sha256()
    if report.get("manifest_sha256") != file_sha256(BankLayout(bank_dir).manifest):
        raise CampaignError(
            E_VERIFY, f"{bank.bank_id}: the verify report is stale (run verify-all)"
        )
    if report.get("ok") and report.get("bank_sha256") != digest:
        raise CampaignError(E_VERIFY, f"{bank.bank_id}: the verify report names another bank hash")
    config_ok = manifest.generation_config_sha256 == plan.freeze.config_frozen_sha256
    if not config_ok:
        problems.append(f"{bank.bank_id}: config hash differs from the G4 freeze value")
    if manifest.seed_namespace != bank.seed_namespace or manifest.bank_version != bank.bank_version:
        problems.append(f"{bank.bank_id}: seed namespace or version differs from the plan")
    if (
        manifest.permutation_sha256 != bank.permutation_sha256
        or manifest.dyad_slot != bank.dyad_slot
    ):
        problems.append(f"{bank.bank_id}: unit differs from the plan")
    max_slots = max((a.slots_used for a in manifest.attempts), default=0)
    if len(manifest.attempts) > B_MAX_ATTEMPTS:
        problems.append(f"{bank.bank_id}: {len(manifest.attempts)} attempts (cap 4)")
    if max_slots > B_SLOTS_PER_ATTEMPT:
        problems.append(f"{bank.bank_id}: an attempt used {max_slots} slots (cap 576)")
    if not report.get("ok"):
        problems.append(f"{bank.bank_id}: banks verify failed")
    crashed = versions[:-1]
    crashed_slots = sum(bank_progress(layout, v, active=False).slots_total for v in crashed)
    used = next((a for a in manifest.attempts if a.attempt == manifest.attempt_used), None)
    row = {
        "sequence": bank.sequence,
        "bank_id": bank.bank_id,
        "role": bank.role,
        "dyad_slot": bank.dyad_slot,
        "bank_version": bank.bank_version,
        "status": manifest.status,
        "assignable": int(manifest.status == "complete" and bool(report.get("ok")) and config_ok),
        "generation_config_sha256": manifest.generation_config_sha256,
        "config_matches_freeze": int(config_ok),
        "attempts": len(manifest.attempts),
        "attempt_used": manifest.attempt_used,
        "slots_attempt_used": None if used is None else used.slots_used,
        "max_slots_per_attempt": max_slots,
        "slots_total": sum(a.slots_used for a in manifest.attempts),
        "crashed_versions": len(crashed),
        "crashed_slots": crashed_slots,
        "verify": "ok" if report.get("ok") else "failed",
        "verify_report_sha256": file_sha256(report_path),
        "bank_sha256": digest,
    }
    return row, problems


def read_escalation(root: str | os.PathLike[str]) -> dict[str, Any] | None:
    path = CampaignLayout.at(root).escalation
    if not path.is_file():
        return None
    doc = read_json(path)
    return doc if isinstance(doc, dict) else None


def compile_register(root: str | os.PathLike[str], *, clock: Clock) -> RegisterResult:
    """Compile the register, archive and report (module docstring, step 2)."""
    layout = CampaignLayout.at(root)
    if layout.lock.exists():
        raise CampaignError(E_STATE, "a runner holds runner.lock; wait for it to finish")
    plan, history = _versions(root)
    states = {
        b.bank_id: bank_progress(layout, history[b.bank_id][-1], active=False).state
        for b in plan.banks
    }
    open_banks = sorted(b for b, s in states.items() if s not in ("complete", "unavailable"))
    if open_banks:
        raise CampaignError(
            E_STATE,
            f"{len(open_banks)} banks are not finished ({', '.join(open_banks[:5])}"
            f"{' ...' if len(open_banks) > 5 else ''}): run them, or rebuild crashed banks",
        )
    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    for bank in plan.banks:
        row, row_problems = register_row(layout, plan, history[bank.bank_id])
        rows.append(row)
        problems.extend(row_problems)
    entries = {
        f"{v.bank_id}@{v.bank_version}": (
            SeedEntry(v.bank_id, v.bank_version, v.seed_namespace),
            layout.bank_dir(v.run_id, v.bank_id),
        )
        for versions in history.values()
        for v in versions
    }
    used: UsedSeeds = check_used_seeds(entries, pilot_namespaces=plan.pilot.namespaces)
    used_sha = write_document(layout.used_seeds, used.to_dict())
    if not used.ok:  # the details name seed keys: they stay in used-seeds.json
        problems.append(
            f"used-seed check failed ({len(used.problems)} problems, see used-seeds.json)"
        )
    if len(rows) != N_BANKS:
        problems.append(f"{len(rows)} bank records, not {N_BANKS}")
    timing = timing_log(root)
    csv_sha = _write_csv(layout.register_csv, REGISTER_COLUMNS, rows)
    counts = tally(rows)
    escalation = read_escalation(root)
    decision = decide(counts, checks_ok=not problems, escalated=escalation is not None)
    archive = write_archive(layout.root, layout.archive(plan.campaign_id))
    verification = read_json(layout.verification) if layout.verification.is_file() else {}
    doc: dict[str, Any] = {
        "format": REGISTER_FORMAT,
        "format_version": REGISTER_VERSION,
        "campaign_id": plan.campaign_id,
        "set": plan.set,
        "demo": plan.demo,
        "compiled_utc": utc_text(clock.utc_now()),
        "generation_config_sha256": plan.generation_config_sha256,
        "freeze": {
            "manifest_sha256": plan.freeze.manifest_sha256,
            "freeze_version": plan.freeze.freeze_version,
            "status": plan.freeze.status,
            "tag": plan.freeze.tag,
            "repo_commit": plan.freeze.repo_commit,
            "config_frozen_sha256": plan.freeze.config_frozen_sha256,
            "tag_checked": plan.freeze.tag_checked,
            "tag_commit": plan.freeze.tag_commit,
            "guard_checked": plan.freeze.guard_checked,
        },
        "counts": to_json_value(counts),
        "checks": {
            "bank_records": len(rows) == N_BANKS,
            "attempts_within_cap": all(int(r["attempts"]) <= B_MAX_ATTEMPTS for r in rows),
            "slots_within_cap": all(
                int(r["max_slots_per_attempt"]) <= B_SLOTS_PER_ATTEMPT for r in rows
            ),
            "config_matches_freeze": all(r["config_matches_freeze"] == 1 for r in rows),
            "complete_banks_verified": all(
                r["verify"] == "ok" for r in rows if r["status"] == "complete"
            ),
            "all_banks_verified": all(r["verify"] == "ok" for r in rows),
            "seeds_unique_and_disjoint": used.ok,
        },
        "problems": problems[:50],
        "decision": decision,
        "escalation": {
            "required": escalation_required(counts),
            "rule": ESCALATION_RULE,
            "complete": counts.complete,
            "needed": MIN_COMPLETE,
            "reference": None if escalation is None else escalation.get("reference"),
            "date": None if escalation is None else escalation.get("date"),
        },
        "unavailable_bank_ids": [r["bank_id"] for r in rows if r["status"] == "unavailable"],
        "spare_rule": SPARE_RULE,
        "crashed_versions": sum(int(r["crashed_versions"]) for r in rows),
        "slots_total": int(timing.totals["slots"]),
        "hashes": {
            "plan_sha256": file_sha256(layout.plan),
            "seed_check_sha256": file_sha256(layout.seed_check),
            "used_seeds_sha256": used_sha,
            "register_csv_sha256": csv_sha,
            "verification_log_sha256": verification.get("log_sha256"),
            "verification_sha256": file_sha256(layout.verification)
            if layout.verification.is_file()
            else None,
            "timing_csv_sha256": timing.timing_sha256,
            "slot_timing_csv_sha256": timing.slot_timing_sha256,
            "archive_sha256": archive.sha256,
        },
        "archive": to_json_value(archive),
    }
    require_schema(REGISTER_SCHEMA, doc)
    json_sha = write_document(layout.register_json, doc)
    _write_report(layout, doc)
    return RegisterResult(decision, counts, csv_sha, json_sha, archive, tuple(problems))


def _yes(flag: object) -> str:
    return "yes" if flag else "no"


def _write_report(layout: CampaignLayout, doc: Mapping[str, Any]) -> None:
    c = doc["counts"]
    e = doc["escalation"]
    f = doc["freeze"]
    h = doc["hashes"]
    unavailable = ", ".join(doc["unavailable_bank_ids"]) or "none"
    lines = [
        f"# Study B confirmatory banks: {doc['campaign_id']}",
        "",
        f"Decision: **{doc['decision']}**",
        "",
        "| Banks | Complete | Unavailable |",
        "| --- | ---: | ---: |",
        f"| Main (64) | {c['main_complete']} | {c['main_unavailable']} |",
        f"| Spares (8) | {c['spares_complete']} | {c['spares_unavailable']} |",
        f"| All ({c['banks']}) | {c['complete']} | {c['unavailable']} |",
        "",
        f"- Escalation rule: {e['rule']}. Complete: {e['complete']} of {e['needed']} needed; "
        + ("escalation required." if e["required"] else "no escalation needed.")
        + (f" Recorded: {e['reference']} ({e['date']})." if e["reference"] else ""),
        f"- Unavailable banks (never assigned; log each with `log_bank_unavailable` before the "
        f"first reveal): {unavailable}.",
        f"- Generation config: `{doc['generation_config_sha256']}`; G4 freeze manifest "
        f"`{f['manifest_sha256']}` ({f['status']}, "
        + (f"tag `{f['tag']}`" if f["tag"] else "no tag")
        + f"); freeze tag checked in the repository: {_yes(f['tag_checked'])}"
        + (f" (tagged commit `{f['tag_commit']}`)" if f["tag_commit"] else "")
        + f"; #25 freeze guard run: {_yes(f['guard_checked'])}.",
        f"- Register CSV: `{h['register_csv_sha256']}`; archive `{doc['archive']['name']}`: "
        f"`{h['archive_sha256']}` ({doc['archive']['files']} files).",
        f"- Verification log: `{h['verification_log_sha256']}`; "
        f"timing log: `{h['timing_csv_sha256']}`.",
        f"- Slots used: {doc['slots_total']}; crashed bank versions: {doc['crashed_versions']}.",
    ]
    if doc["problems"]:
        lines += ["", "Problems:", *(f"- {p}" for p in doc["problems"])]
    lines += [
        "",
        "Next: commit `register.csv` and `register.json` (`publish`), then check the commit "
        "time against the first confirmatory screening (`commit-check`).",
    ]
    with open(layout.g5b_report, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


def record_escalation(
    root: str | os.PathLike[str], *, reference: str, date: str, clock: Clock
) -> Path:
    """Record the advisor escalation (role, link, date; never a name). Refused when at
    least 64 banks are complete or the register has not been compiled."""
    layout = CampaignLayout.at(root)
    if not layout.register_json.is_file():
        raise CampaignError(E_STATE, "compile the register first")
    register = read_json(layout.register_json)
    if not register["escalation"]["required"]:
        raise CampaignError(E_STATE, "at least 64 banks are complete: no escalation needed")
    if not REFERENCE_RE.fullmatch(reference):
        raise CampaignError(
            E_INPUT, "the reference is a link to an issue or PR comment in this repository"
        )
    if not DATE_RE.fullmatch(date):
        raise CampaignError(E_INPUT, "the date is YYYY-MM-DD")
    doc = {
        "format": ESCALATION_FORMAT,
        "format_version": 1,
        "role": "advisor",
        "reference": reference,
        "date": date,
        "complete": register["escalation"]["complete"],
        "needed": MIN_COMPLETE,
        "recorded_utc": utc_text(clock.utc_now()),
    }
    write_document(layout.escalation, doc, exclusive=True)
    append_event(
        layout,
        {"event": "escalation_recorded", "at_utc": doc["recorded_utc"], "reference": reference},
    )
    return layout.escalation


# ---------------------------------------------------------------------------
# Publishing and the commit timestamp


PUBLIC_FILES: Final = ("register.csv", "register.json")


def publish_register(
    root: str | os.PathLike[str], dest: str | os.PathLike[str]
) -> tuple[Path, ...]:
    """Copy the public register files (hashes and counts only) to `dest` in the
    repository. Refused for a `blocked` register or one whose CSV hash is not the one
    `register.json` names."""
    layout = CampaignLayout.at(root)
    if not layout.register_json.is_file():
        raise CampaignError(E_STATE, "compile the register first")
    register = read_json(layout.register_json)
    if register["decision"] == "blocked":
        raise CampaignError(E_STATE, f"the register is blocked: {register['problems'][:3]}")
    if file_sha256(layout.register_csv) != register["hashes"]["register_csv_sha256"]:
        raise CampaignError(E_STATE, "register.csv is not the file register.json names")
    target = Path(dest)
    target.mkdir(parents=True, exist_ok=True)
    out = []
    for name in PUBLIC_FILES:
        shutil.copyfile(layout.root / name, target / name)
        out.append(target / name)
    return tuple(out)


@dataclass(frozen=True, slots=True)
class RegisterCommit:
    """`check_register_commit`: the commit that added a register file."""

    path: str
    commit: str | None
    committed_utc: str | None
    url: str | None
    sha256: str | None
    changed_after: int
    """Later commits that touched the file (0 for an untouched register)."""
    first_screening_utc: str | None
    ok: bool
    problems: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = to_json_value(self)
        return data


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)  # noqa: S603


def _utc(text: str) -> datetime:
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise CampaignError(E_INPUT, f"{text!r}: give a time zone (e.g. 2027-05-03T09:00:00Z)")
    return moment.astimezone(UTC)


def check_register_commit(
    repo: str | os.PathLike[str],
    path: str,
    *,
    expected_sha256: str | None = None,
    first_screening_utc: str | None = None,
    ref: str = "HEAD",
) -> RegisterCommit:
    """Find the commit (reachable from `ref`) that added `path` (POSIX, relative to the
    repository root) and check it: the committed bytes have `expected_sha256` (the
    campaign's file), no later commit changed the file, and the commit time precedes
    `first_screening_utc` (strictly). `ok` needs both: without `expected_sha256` or
    `first_screening_utc` the result lists what was not checked and is not ok. The
    commit time is the committer date git records; the push time on the repository host
    is the independent record (see the docs)."""
    repo_path = Path(repo)
    rel = path.replace("\\", "/").strip("/")
    log = _git(repo_path, "log", "--format=%H %ct", ref, "--", rel)
    if log.returncode != 0:
        raise CampaignError(
            E_COMMIT, f"git log failed: {log.stderr.decode(errors='replace')[:200]}"
        )
    commits = [line.split() for line in log.stdout.decode().splitlines() if line.strip()]
    problems: list[str] = []
    if expected_sha256 is None:
        problems.append("file not checked: no expected SHA-256 (the campaign's file) given")
    if first_screening_utc is None:
        problems.append("timestamp not checked: no first confirmatory screening time given")
    if not commits:
        return RegisterCommit(
            rel,
            None,
            None,
            None,
            None,
            0,
            first_screening_utc,
            False,
            (f"{rel} was never committed", *problems),
        )
    first, epoch = commits[-1][0], int(commits[-1][1])
    committed = datetime.fromtimestamp(epoch, UTC)
    shown = _git(repo_path, "show", f"{first}:{rel}")
    digest = hashlib.sha256(shown.stdout).hexdigest() if shown.returncode == 0 else None
    if digest is None:
        problems.append(f"{rel} is not in commit {first} (it was deleted, not added)")
    elif expected_sha256 is not None and digest != expected_sha256:
        problems.append(
            f"the committed {rel} is not the campaign's file ({digest} != {expected_sha256})"
        )
    if len(commits) > 1:
        problems.append(
            f"{rel} changed in {len(commits) - 1} later commits; the first commit is the record"
        )
    if first_screening_utc is not None and not committed < _utc(first_screening_utc):
        problems.append(
            f"the register commit ({utc_text(committed)}) does not precede the first screening"
        )
    return RegisterCommit(
        path=rel,
        commit=first,
        committed_utc=utc_text(committed),
        url=f"{REPO_URL}/commit/{first}",
        sha256=digest,
        changed_after=len(commits) - 1,
        first_screening_utc=first_screening_utc,
        ok=not problems,
        problems=tuple(problems),
    )
