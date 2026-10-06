"""Reconciled and derived tables of a data root (#33).

``av-analysis derive --root DIR`` reads every ``reconciled/<visit_id>/reconciliation.json``
with its raw logs and reference inputs and writes, through ``derived.table_bytes`` and
``paths.write_output``:

* ``reconciled/visit-status.csv``, ``reconciled/discrepancies.csv``,
  ``reconciled/exposure-cumulative.csv``, ``reconciled/enrollment.csv`` and
  ``reconciled/manifest.json``;
* ``derived/trials.csv``, ``derived/endpoints.csv`` and ``derived/manifest.json``
  (``outputs-manifest.schema.json``).

A report is refused (rerun ``reconcile``) when a listed input no longer has the hash it
recorded, or when a raw file the run would now read (the visit's folder, the person's
earlier visits, the other dyad member's visit, ``raw/deviations-log.csv``) or a reference
input it reported missing has appeared since. ``visit-status`` has a row for every
expected visit of every revealed person (held when the raw folder exists; ``missed`` or
``withdrawn`` from deviation records; ``pending`` otherwise). ``trials`` has a row per
trial-log row of each reconciled visit that is a scheduled item or a retry linked to one,
plus a ``row_source`` deviation
row for each scheduled opportunity lost to a verified apparatus or logger failure
(``reconcile``), and ``endpoints`` counts it as accounted and faulted; opportunities never
undertaken after withdrawal get no row and leave the battery partial or missing
(``withdrawn_mid_battery``); the deviation records of a visit are looked up in its own
scope (``reconcile_checks.visit_records``), never by ID across visits. ``enrollment``
counts eligibility records and reveals from the reveal log. Derived tables carry no
condition labels; the dashboard (#35) and the analysis pipeline (#34) read only these
files, never raw logs. ``av-analysis refresh`` (``cli``) runs reconcile, this command and
the dashboard in order. Computation rules per column are in
``analysis/docs/reconciliation.md``.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC
from pathlib import Path
from typing import Any, Final

from av_schedules.orders import PLAYS
from av_schedules.planning import booked_minutes

from . import __version__
from .derived import TABLES, Row, table_bytes
from .fileio import json_bytes, read_bytes, sha256_bytes
from .ledger import Prior, VisitLogs, fold, person_rows, visit_logs
from .loaders import RefusedInputError, load_deviations_log, load_raw_visit
from .paths import (
    DEVIATIONS_LOG,
    OUTPUTS_MANIFEST,
    RECONCILIATION_REPORT,
    DataRoot,
    WatermarkError,
    write_output,
)
from .reconcile import related_visits
from .reconcile_checks import (
    LOST_OPPORTUNITY_CATEGORIES,
    WITHDRAWAL_CATEGORIES,
    Record,
    records_of,
    scope_of,
    visit_date,
    visit_records,
    visit_times,
)
from .references import (
    ReferenceError,
    References,
    cue_of,
    load_references,
    load_reveal,
    revealed_persons,
    schedule_items,
    set_of_unit,
)
from .schemas import OUTPUTS_MANIFEST_FORMAT, OUTPUTS_MANIFEST_FORMAT_VERSION, validator
from .vocab import (
    BATTERIES,
    CONSUMING_AUDIBLE_STATUS,
    FAULT_TYPES,
    FREEZE_FAULT_MS,
    OVERRUN_MINUTES,
    VISITS,
    fault_type,
    parse_timestamp,
    split_fault_codes,
)
from .windows import classify, window, yoked_gap_hours, yoked_gap_ok

TEST_TYPES: Final = ("pre_old", "trained", "novel", "atomic", "no_cue", "speech")
VERIFIED_STATUS: Final = ("confirmed_audible", "estimated")
# Discrepancy codes on a trial (or one of its plays) that invalidate its delivery.
INVALIDATING: Final = frozenset(
    {
        "WAVEFORM_HASH_MISMATCH",
        "WAVEFORM_HASH_MISSING",
        "COUNT_MISSING_PLAY",
        "COUNT_EXTRA_PLAY",
        "PLAYBACK_STATUS_CONFLICT",
        "RESPONSE_EVENT_MISSING",
    }
)
STAGE_BLOCK: Final[dict[str, str]] = {
    "profile_menu": "profile_menu",
    "atom_menu": "atom_menus",
    "atomic_lesson": "atomic_lessons",
    "message_lesson": "message_lessons",
    "pre_old": "pre_old",
    "trained": "trained",
    "novel": "novel",
    "atomic": "atomic",
    "no_cue": "validity",
    "speech": "validity",
}


def _bool(text: str) -> bool | None:
    return {"true": True, "false": False}.get(text)


def _int(text: str) -> int | None:
    try:
        return int(text) if text != "" else None
    except ValueError:
        return None


def _float(text: str) -> float | None:
    try:
        return float(text) if text != "" else None
    except ValueError:
        return None


def _enum(spec: str, column: str, value: str) -> str | None:
    values = TABLES[spec].column(column).values or ()
    return value if value in values else None


def fault_info(row: Mapping[str, str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(fault codes, fault types) of a trial-log row (``derived.TRIALS`` rules)."""
    try:
        codes = split_fault_codes(row.get("technical_fault_code", ""))
    except ValueError:
        codes = ()
    types = {fault_type(c) for c in codes}
    freeze = _int(row.get("frame_freeze_ms", ""))
    if freeze is not None and freeze > FREEZE_FAULT_MS:
        types.add("presentation_freeze")
    if row.get("reset_ok") == "false":
        types.add("failed_reset")
    return codes, tuple(t for t in FAULT_TYPES if t in types)


# ---------------------------------------------------------------------------------------
# Inputs


@dataclasses.dataclass
class _Person:
    person_id: str
    study: str
    coded_id: str
    refs: References | None
    held: dict[str, VisitLogs]
    reports: dict[str, Mapping[str, Any]]
    report_sha: dict[str, str]


def _report(root: DataRoot, visit_id: str) -> tuple[Mapping[str, Any], str] | None:
    path = root.output_path("reconciled", f"{visit_id}/{RECONCILIATION_REPORT}")
    if not path.is_file():
        return None
    data = read_bytes(path)
    doc = json.loads(data)
    if not isinstance(doc, dict) or doc.get("data_kind") != root.data_kind:
        raise RefusedInputError(f"{visit_id}: reconciliation report of another data kind")
    listed = {entry["path"] for entry in doc.get("inputs", [])}
    for entry in doc.get("inputs", []):
        area, _, rel = entry["path"].partition("/")
        if area not in ("raw", "inputs"):
            continue
        current = root.input_path(area, rel)
        if not current.is_file() or sha256_bytes(read_bytes(current)) != entry["sha256"]:
            raise RefusedInputError(
                f"{visit_id}: {entry['path']} changed since the reconciliation report was "
                "written; rerun reconcile"
            )
    for rel in _appeared(root, visit_id, doc, listed):
        raise RefusedInputError(
            f"{visit_id}: {rel} appeared since the reconciliation report was written; "
            "rerun reconcile"
        )
    return doc, sha256_bytes(data)


def _appeared(root: DataRoot, visit_id: str, doc: Mapping[str, Any], listed: set[str]) -> list[str]:
    """Inputs a rerun of ``reconcile`` would read now that the report does not list: raw
    files of the folders it reads (the visit, the person's earlier visits, the other dyad
    member's visit), the study-wide log, and reference inputs it reported missing."""
    earlier, partner = related_visits(visit_id)
    now: list[str] = []
    for vid in (visit_id, *earlier, *([partner] if partner else [])):
        folder = root.raw_visit_dir(vid)
        if folder.is_dir():
            now += [
                path.relative_to(root.path).as_posix()
                for path in sorted(folder.rglob("*"))
                if path.is_file()
            ]
    if root.input_path("raw", DEVIATIONS_LOG).is_file():
        now.append(f"raw/{DEVIATIONS_LOG}")
    for d in doc.get("discrepancies", []):
        if d.get("code") != "REFERENCE_INPUT":
            continue
        for row in d.get("rows", []):
            area, _, rel = row.partition("/")
            if area == "inputs" and root.input_path("inputs", rel).is_file():
                now.append(row)
    return [rel for rel in now if rel not in listed]


def _persons(root: DataRoot) -> list[_Person]:
    out: list[_Person] = []
    for person, coded in revealed_persons(root).items():
        study = person[0]
        held: dict[str, VisitLogs] = {}
        reports: dict[str, Mapping[str, Any]] = {}
        shas: dict[str, str] = {}
        refs: References | None = None
        for visit in VISITS[study]:  # type: ignore[index]
            vid = f"{person}-{visit}"
            try:
                held[visit] = visit_logs(load_raw_visit(root, vid))
            except FileNotFoundError:
                continue
            found = _report(root, vid)
            if found is not None:
                reports[visit], shas[visit] = found
        first = VISITS[study][0]  # type: ignore[index]
        try:
            refs = load_references(root, f"{person}-{first}")
        except ReferenceError:
            refs = None
        out.append(_Person(person, study, coded, refs, held, reports, shas))
    return out


def _records(p: _Person, log: Sequence[Record]) -> list[Record]:
    """Every deviation record of the person's held visits and the study-wide log (visit
    states: withdrawal and missed-visit records)."""
    out: list[Record] = []
    for logs in p.held.values():
        out.extend(records_of(logs.raw.deviations))
    out.extend(log)
    return out


def _scoped(p: _Person, visit: str, log: Sequence[Record]) -> list[Record]:
    """The deviation records that concern one visit of the person, as ``reconcile`` links
    them (``reconcile_checks.visit_records``): the visit's own ``deviations.csv`` and the
    study-wide log records about the visit, a row of it, the person or the participant."""
    logs = p.held.get(visit)
    scope = scope_of(logs, f"{p.person_id}-{visit}", [p.coded_id])
    return visit_records(logs.raw.deviations if logs is not None else None, log, scope)


def _by_id(records: Sequence[Record]) -> dict[str, Record]:
    """Deviation ID -> record (the first one: a visit's own record before the log; C1
    reports an ID used twice)."""
    out: dict[str, Record] = {}
    for r in records:
        out.setdefault(r.deviation_id, r)
    return out


# ---------------------------------------------------------------------------------------
# Visit facts


@dataclasses.dataclass(frozen=True)
class _Timing:
    visit_date: str | None
    anchor_visit: str | None
    days: int | None
    lo: int | None
    hi: int | None
    timing: str


def _timing(p: _Person, visit: str) -> _Timing:
    w = window(p.study, visit)
    this = visit_date(p.held.get(visit))
    if w is None:
        return _Timing(this.isoformat() if this else None, None, None, None, None, "not_applicable")
    anchor = visit_date(p.held.get(w.anchor))
    days = (this - anchor).days if this and anchor else None
    return _Timing(
        this.isoformat() if this else None,
        w.anchor,
        days,
        w.lo_days,
        w.hi_days,
        classify(p.study, visit, this, anchor),
    )


def _state(p: _Person, visit: str, records: Sequence[Record]) -> str:
    if visit in p.held:
        return "held"
    order = VISITS[p.study]  # type: ignore[index]
    vid = f"{p.person_id}-{visit}"
    for r in records:
        if r.category != "withdrawal":
            continue
        person_level = r.event_id == p.person_id or (
            not r.event_id and r.participant_id and r.participant_id == p.coded_id
        )
        if person_level:
            return "withdrawn"
        for earlier in order[: order.index(visit) + 1]:
            if r.event_id == f"{p.person_id}-{earlier}":
                return "withdrawn"
    if any(r.category == "missed_visit" and r.event_id == vid for r in records):
        return "missed"
    return "pending"


# ---------------------------------------------------------------------------------------
# Trials


def _augmented(p: _Person, visit: str, lost: Mapping[str, Record]) -> VisitLogs:
    """The visit's logs with a placeholder row (no plays) for each lost opportunity,
    inserted in schedule order, so the fold gives it prior counts."""
    logs = p.held[visit]
    if not lost or p.refs is None:
        return logs
    index = {
        item["trial_id"]: i for i, (_, item) in enumerate(schedule_items(p.refs.schedules[visit]))
    }
    items = {item["trial_id"]: item for _, item in schedule_items(p.refs.schedules[visit])}
    rows = list(logs.trials)
    for tid in sorted(lost, key=lambda t: index[t]):
        pos = next(
            (
                i
                for i, r in enumerate(rows)
                if index.get(r.get("retry_of") or r.get("trial_id", ""), -1) > index[tid]
            ),
            len(rows),
        )
        rows.insert(pos, {"trial_id": tid, "message_id": cue_of(items[tid]), "retry_of": ""})
    return dataclasses.replace(logs, trials=tuple(rows))


def _lost(p: _Person, visit: str, records: Mapping[str, Record]) -> dict[str, Record]:
    """Scheduled trial ID -> the deviation record that verifies its loss."""
    out: dict[str, Record] = {}
    report = p.reports.get(visit)
    if report is None:
        return out
    for d in report["discrepancies"]:
        if d["code"] != "COUNT_MISSING_TRIAL" or not d["resolved"]:
            continue
        record = records.get(d["deviation_id"] or "")
        if record is not None and record.category in LOST_OPPORTUNITY_CATEGORIES:
            out[d["rows"][0]] = record
    return out


def _named(report: Mapping[str, Any] | None) -> dict[str, list[Mapping[str, Any]]]:
    out: dict[str, list[Mapping[str, Any]]] = {}
    for d in (report or {}).get("discrepancies", []):
        for name in d["rows"]:
            out.setdefault(name, []).append(d)
    return out


def _trial_rows(
    root: DataRoot, p: _Person, scoped: Mapping[str, Mapping[str, Record]]
) -> list[Row]:
    refs = p.refs
    if refs is None:
        return []
    order = VISITS[p.study]  # type: ignore[index]
    lost = {v: _lost(p, v, scoped[v]) for v in p.reports}
    visits = [_augmented(p, v, lost.get(v, {})) for v in order if v in p.held]
    result = fold(visits)
    rows: list[Row] = []
    for visit in (v for v in order if v in p.reports):
        logs = p.held[visit]
        doc = refs.schedules.get(visit)
        if doc is None:
            continue
        timing = _timing(p, visit)
        sched = {
            item["trial_id"]: (block, b_pos, item)
            for b_pos, block in enumerate(doc["blocks"], start=1)
            for item in block["items"]
        }
        named = _named(p.reports[visit])
        records = scoped[visit]
        plays = logs.plays_by_trial() if logs.linked else {}
        known = {r.get("trial_id", "") for r in logs.trials}
        common: Row = {
            "data_kind": root.data_kind,
            "study": p.study,
            "set": refs.set_name,
            "unit_id": refs.unit_id,
            "book_id": refs.package_id,
            "person_id": p.person_id,
            "visit": visit,
            "visit_seq": order.index(visit) + 1,
            "visit_id": f"{p.person_id}-{visit}",
            "visit_date": timing.visit_date,
            "days_since_anchor": timing.days,
            "timing": timing.timing,
        }
        seen: set[str] = set()
        for row in logs.trials:
            tid = row.get("trial_id", "")
            target = row.get("retry_of") or tid
            if target not in sched or tid in seen or (row.get("retry_of") and target not in known):
                continue  # extra trials and unlinked retries are not derived rows
            seen.add(tid)
            block, b_pos, item = sched[target]
            mine = plays.get(tid, [])
            names = [tid, *[pl.get("event_id", "") for pl in mine]]
            found = [d for n in names for d in named.get(n, [])]
            prior = result.prior.get((visit, tid))
            rows.append(
                {
                    **common,
                    **_item_columns(block, b_pos, item),
                    "session_id": row.get("session_id") or None,
                    "trial_id": tid,
                    "retry_of": row.get("retry_of") or None,
                    "row_source": "logged",
                    **_logged_columns(row, item, mine, logs.linked, found, prior),
                    "deviation_ids": _deviation_ids([row.get("deviation_id", "")], found, records),
                    "discrepancy_codes": tuple(sorted({d["code"] for d in found})),
                }
            )
        for tid, record in sorted(lost.get(visit, {}).items()):
            block, b_pos, item = sched[tid]
            prior = result.prior.get((visit, tid))
            found = named.get(tid, [])
            phrase = prior.phrase if prior else 0
            rows.append(
                {
                    **common,
                    **_item_columns(block, b_pos, item),
                    "session_id": (logs.raw.exit_manifest or {}).get("session_id"),
                    "trial_id": tid,
                    "retry_of": None,
                    "row_source": "deviation",
                    **dict.fromkeys(_RESPONSE_COLUMNS),
                    "playback_status": None,
                    "fault_codes": ("OPPORTUNITY_LOST",),
                    "fault_types": ("other",),
                    "valid_delivery": False,
                    "exposure_consumed": record.prior_audio_exposure != "none",
                    "novelty": (
                        ("first" if phrase == 0 else "repeat")
                        if item["trained_status"] == "heldout"
                        else None
                    ),
                    "prior_phrase_exposures": phrase,
                    "prior_atom_exposures": prior.atom if prior else 0,
                    "actual_delay_hours": None,
                    "deviation_ids": _deviation_ids([], found, records),
                    "discrepancy_codes": tuple(sorted({d["code"] for d in found})),
                }
            )
    return rows


_RESPONSE_COLUMNS: Final = (
    "response_code",
    "response_action",
    "response_target",
    "exact_correct",
    "action_correct",
    "referent_correct",
    "scheduled_onset_mono_ms",
    "audio_onset_estimate_mono_ms",
    "onset_uncertainty_ms",
    "audio_offset_mono_ms",
    "commit_mono_ms",
    "response_time_ms",
)


def _item_columns(block: Mapping[str, Any], b_pos: int, item: Mapping[str, Any]) -> dict[str, Any]:
    intended = item.get("intended") or {}
    target_action = target_referent = None
    if intended.get("kind") == "message":
        target_action, target_referent = intended["semantic_action"], intended["semantic_referent"]
    elif intended.get("kind") == "atom":
        if intended["role"] == "action":
            target_action = intended["semantic_label"]
        else:
            target_referent = intended["semantic_label"]
    cue = cue_of(item)
    kind = (
        "message"
        if item.get("message_id")
        else "atom"
        if item.get("atom_id")
        else "speech"
        if item.get("speech_id")
        else "none"
    )
    return {
        "block": block["block"],
        "block_position": b_pos,
        "position": item["position"],
        "pass": item["pass"],
        "trial_type": item["trial_type"],
        "family": intended.get("family"),
        "item_id": cue or None,
        "item_kind": kind,
        "trained_status": item["trained_status"],
        "target_action": target_action,
        "target_referent": target_referent,
    }


def _logged_columns(
    row: Mapping[str, str],
    item: Mapping[str, Any],
    plays: Sequence[Mapping[str, str]],
    linked: bool,
    found: Sequence[Mapping[str, Any]],
    prior: Prior | None,
) -> dict[str, Any]:
    trial_type = item["trial_type"]
    codes, types = fault_info(row)
    status = row.get("playback_status", "")
    statuses = [p.get("audible_status", "") for p in plays]
    expected = PLAYS[trial_type]
    if trial_type == "no_cue":
        playback_ok = True
    else:
        playback_ok = (
            linked
            and status == "observed_complete"
            and len(plays) >= expected
            and all(s in VERIFIED_STATUS for s in statuses)
        )
    response_ok = trial_type not in TEST_TYPES or (
        row.get("response_code", "") != "" and "missing_response_log" not in types
    )
    invalid = any(d["code"] in INVALIDATING for d in found)
    if statuses:
        consumed = any(s in CONSUMING_AUDIBLE_STATUS for s in statuses)
    else:
        consumed = status in ("observed_complete", "uncertain")
    phrase = prior.phrase if prior is not None else 0
    return {
        "response_code": _enum("trials", "response_code", row.get("response_code", "")),
        "response_action": _enum("trials", "response_action", row.get("response_action", "")),
        "response_target": _enum("trials", "response_target", row.get("response_target", "")),
        "exact_correct": _bool(row.get("exact_correct", "")),
        "action_correct": _bool(row.get("action_correct", "")),
        "referent_correct": _bool(row.get("referent_correct", "")),
        "scheduled_onset_mono_ms": _int(row.get("scheduled_onset_mono_ms", "")),
        "audio_onset_estimate_mono_ms": _int(row.get("audio_onset_estimate_mono_ms", "")),
        "onset_uncertainty_ms": _nonneg(_int(row.get("onset_uncertainty_ms", ""))),
        "audio_offset_mono_ms": _int(row.get("audio_offset_mono_ms", "")),
        "commit_mono_ms": _int(row.get("commit_mono_ms", "")),
        "response_time_ms": _int(row.get("response_time_ms", "")),
        "playback_status": _enum("trials", "playback_status", status),
        "fault_codes": codes,
        "fault_types": types,
        "valid_delivery": bool(playback_ok and response_ok and not types and not invalid),
        "exposure_consumed": consumed,
        "novelty": (
            ("first" if phrase == 0 else "repeat") if item["trained_status"] == "heldout" else None
        ),
        "prior_phrase_exposures": phrase,
        "prior_atom_exposures": prior.atom if prior is not None else 0,
        "actual_delay_hours": _float(row.get("actual_delay_hours", "")),
    }


def _nonneg(value: int | None) -> int | None:
    return value if value is None or value >= 0 else None


def _deviation_ids(
    own: Sequence[str], found: Sequence[Mapping[str, Any]], records: Mapping[str, Record]
) -> tuple[str, ...]:
    ids = {i for i in own if i and i in records}
    ids |= {d["deviation_id"] for d in found if d["deviation_id"] and d["resolved"]}
    return tuple(sorted(ids))


# ---------------------------------------------------------------------------------------
# Endpoints, visit status, discrepancies


def _missing_reason(
    p: _Person, visit: str, battery: str, state: str, records: Mapping[str, Record]
) -> str:
    if state != "held":
        return state  # withdrawn, missed or pending
    report = p.reports.get(visit)
    if report is None:
        return "pending"
    sched_block = {
        item["trial_id"]: block["block"]
        for block in (p.refs.schedules[visit]["blocks"] if p.refs else [])
        for item in block["items"]
    }
    missing = [
        d
        for d in report["discrepancies"]
        if d["code"] == "COUNT_MISSING_TRIAL" and sched_block.get(d["rows"][0]) == battery
    ]
    for d in missing:
        record = records.get(d["deviation_id"] or "")
        if d["resolved"] and record is not None and record.category in WITHDRAWAL_CATEGORIES:
            return "withdrawn_mid_battery"
    closed = (p.held[visit].raw.exit_manifest or {}).get("closed")
    if any(not d["resolved"] for d in missing) and closed == "interrupted":
        return "technical_stop"
    return "other"


def _endpoint_rows(
    root: DataRoot,
    p: _Person,
    trials: Sequence[Row],
    states: Mapping[str, str],
    scoped: Mapping[str, Mapping[str, Record]],
) -> list[Row]:
    refs = p.refs
    if refs is None:
        return []
    order = VISITS[p.study]  # type: ignore[index]
    rows: list[Row] = []
    for visit in order:
        doc = refs.schedules.get(visit)
        if doc is None:
            continue
        timing = _timing(p, visit)
        mine = [t for t in trials if t["visit"] == visit]
        report = p.reports.get(visit)
        for block in doc["blocks"]:
            battery = block["block"]
            if battery not in BATTERIES:
                continue
            here = [t for t in mine if t["block"] == battery]
            accounted = [t for t in here if t["retry_of"] is None]
            retries = [t for t in here if t["retry_of"] is not None]
            valid_retry = {t["retry_of"] for t in retries if t["valid_delivery"]}
            scheduled = int(block["expected_count"])
            n = len(accounted)
            status = "complete" if n >= scheduled else "partial" if n > 0 else "missing"
            rows.append(
                {
                    "data_kind": root.data_kind,
                    "study": p.study,
                    "set": refs.set_name,
                    "unit_id": refs.unit_id,
                    "book_id": refs.package_id,
                    "person_id": p.person_id,
                    "visit": visit,
                    "visit_seq": order.index(visit) + 1,
                    "visit_id": f"{p.person_id}-{visit}",
                    "battery": battery,
                    "scheduled_n": scheduled,
                    "accounted_n": n,
                    "fault_n": sum(bool(t["fault_codes"] or t["fault_types"]) for t in accounted),
                    "lost_n": sum(t["row_source"] == "deviation" for t in accounted),
                    "valid_delivery_n": sum(
                        bool(t["valid_delivery"] or t["trial_id"] in valid_retry) for t in accounted
                    ),
                    "retry_n": len(retries),
                    "status": status,
                    "missing_reason": None
                    if status == "complete"
                    else _missing_reason(p, visit, battery, states[visit], scoped[visit]),
                    "visit_date": timing.visit_date,
                    "anchor_visit": timing.anchor_visit,
                    "days_since_anchor": timing.days,
                    "window_lo_days": timing.lo,
                    "window_hi_days": timing.hi,
                    "timing": timing.timing,
                    "planned_endpoint": status == "complete"
                    and timing.timing in ("in_window", "not_applicable"),
                    "reconciliation": report["summary"]["status"] if report else "not_run",
                }
            )
    return rows


def _pair(
    p: _Person, visit: str, by_person: Mapping[str, _Person]
) -> tuple[float | None, bool | None]:
    """Study B V1-V3: (hours between the members' session starts, yoked_gap_ok)."""
    if p.study != "B" or visit not in ("V1", "V2", "V3") or p.refs is None:
        return None, None
    other = by_person.get(p.refs.partner_person_id or "")
    if other is None or visit not in p.held or visit not in other.held:
        return None, None
    members = {p.person_id: p.held[visit], other.person_id: other.held[visit]}
    active = members.get(p.refs.active_person_id or "")
    yoked = next((v for k, v in members.items() if k != p.refs.active_person_id), None)
    if active is None or yoked is None:
        return None, None
    a_start, a_end = visit_times(active)
    y_start, _ = visit_times(yoked)
    if a_start is None or a_end is None or y_start is None:
        return None, None
    gap = abs(yoked_gap_hours(a_start, y_start))
    return round(gap, 4), yoked_gap_ok(a_start, a_end, y_start)


def _status_rows(
    root: DataRoot,
    p: _Person,
    trials: Sequence[Row],
    states: Mapping[str, str],
    by_person: Mapping[str, _Person],
    scoped: Mapping[str, Sequence[Record]],
) -> list[Row]:
    order = VISITS[p.study]  # type: ignore[index]
    unit = p.person_id[:5]
    rows: list[Row] = []
    for visit in order:
        timing = _timing(p, visit)
        logs = p.held.get(visit)
        report = p.reports.get(visit)
        summary = report["summary"] if report else None
        mine = [t for t in trials if t["visit"] == visit and t["retry_of"] is None]
        faulted = [t for t in mine if t["fault_codes"] or t["fault_types"]]
        start, end = visit_times(logs) if logs is not None else (None, None)
        booked = booked_minutes(p.study, visit)
        actual = round((end - start).total_seconds() / 60) if start and end else None
        comfort = [r.get("comfort_check", "") for r in (logs.sheet if logs else ())]
        recs = scoped[visit]
        gap, gap_ok = _pair(p, visit, by_person)
        station = (logs.raw.exit_manifest or {}).get("station_id") if logs else None
        rows.append(
            {
                "data_kind": root.data_kind,
                "study": p.study,
                "set": set_of_unit(unit),
                "unit_id": unit,
                "person_id": p.person_id,
                "visit": visit,
                "visit_seq": order.index(visit) + 1,
                "visit_id": f"{p.person_id}-{visit}",
                "station_id": station if isinstance(station, str) else None,
                "visit_state": states[visit],
                "reconciliation": summary["status"] if summary else "not_run",
                "checks_failed": tuple(
                    c["check"]
                    for c in (report["checks"] if report else [])
                    if c["status"] == "fail"
                ),
                "discrepancies_n": summary["discrepancies"] if summary else 0,
                "unresolved_n": summary["unresolved"] if summary else 0,
                "suspension_events": tuple(summary["suspension_events"]) if summary else (),
                "visit_date": timing.visit_date,
                "anchor_visit": timing.anchor_visit,
                "days_since_anchor": timing.days,
                "window_lo_days": timing.lo,
                "window_hi_days": timing.hi,
                "timing": timing.timing,
                "pair_gap_hours": gap,
                "pair_gap_ok": gap_ok,
                "booked_minutes": booked,
                "actual_minutes": actual,
                "overrun": None if actual is None else actual > booked + OVERRUN_MINUTES,
                "opportunities_n": len(mine),
                "fault_n": len(faulted),
                **{
                    f"fault_{f}_n": sum(f in t["fault_types"] for t in mine)  # type: ignore[operator]
                    for f in FAULT_TYPES
                },
                "comfort_flag": (
                    any(c in ("adjusted", "stopped") for c in comfort) if any(comfort) else None
                ),
                "deviations_n": len(recs),
                "open_deviations_n": sum(not r.resolution for r in recs),
                "comfort_deviations_n": sum(r.category == "comfort" for r in recs),
                "withdrawal_deviations_n": sum(r.category == "withdrawal" for r in recs),
                "report_sha256": p.report_sha.get(visit),
            }
        )
    return rows


def _discrepancy_rows(root: DataRoot, p: _Person) -> list[Row]:
    order = VISITS[p.study]  # type: ignore[index]
    rows: list[Row] = []
    for visit, report in sorted(p.reports.items(), key=lambda kv: order.index(kv[0])):
        for d in report["discrepancies"]:
            rows.append(
                {
                    "data_kind": root.data_kind,
                    "study": p.study,
                    "unit_id": p.person_id[:5],
                    "person_id": p.person_id,
                    "visit": visit,
                    "visit_seq": order.index(visit) + 1,
                    "visit_id": f"{p.person_id}-{visit}",
                    "seq": d["seq"],
                    "check": d["check"],
                    "code": d["code"],
                    "rows": tuple(d["rows"]),
                    "deviation_id": d["deviation_id"],
                    "resolved": d["resolved"],
                    "suspension_event": d["suspension_event"],
                    "detail": d["detail"],
                }
            )
    return rows


def _enrollment_rows(root: DataRoot) -> list[Row]:
    rows: list[Row] = []
    for study in ("A", "B"):
        for set_name in ("pilot", "confirmatory"):
            data = load_reveal(root, study, set_name)
            if data is None:
                continue
            doc = data.list_doc
            lines = data.lines
            reveals = list(data.reveals())
            if study == "A":
                planned_units = int(doc["counts"]["batches"])
                planned_persons = int(doc["counts"]["slots"])
            else:
                planned_units = sum(d["kind"] == "dyad" for d in doc["dyads"])
                planned_persons = 2 * planned_units
            eligibility = [ln for ln in lines if ln.get("event") == "eligibility"]
            times = [parse_timestamp(ln["at"]).astimezone(UTC).date() for ln in lines]
            rows.append(
                {
                    "data_kind": root.data_kind,
                    "study": study,
                    "set": set_name,
                    "planned_units_n": planned_units,
                    "planned_persons_n": planned_persons,
                    "eligibility_records_n": len(eligibility),
                    "eligible_persons_n": sum(len(ln["participant_ids"]) for ln in eligibility),
                    "screening_cases_n": None,
                    "revealed_units_n": len({e["unit_id"] for e in reveals}),
                    "revealed_persons_n": len(reveals) * (1 if study == "A" else 2),
                    "spares_used_n": (
                        sum(bool(e.get("replaces")) for e in reveals) if study == "B" else None
                    ),
                    "bank_unavailable_n": (
                        sum(ln.get("event") == "bank_unavailable" for ln in lines)
                        if study == "B"
                        else None
                    ),
                    "last_event_date": max(times).isoformat() if times else None,
                    "reveal_log_sha256": data.log_sha256,
                }
            )
    return rows


def derive_tables(root: DataRoot) -> dict[str, list[Row]]:
    """Table name (``derived.TABLES``) -> rows."""
    persons = _persons(root)
    by_person = {p.person_id: p for p in persons}
    log = records_of(load_deviations_log(root))
    tables: dict[str, list[Row]] = {name: [] for name in TABLES}
    for p in persons:
        visits = VISITS[p.study]  # type: ignore[index]
        all_records = _records(p, log)
        scoped = {v: _scoped(p, v, log) for v in visits}
        by_id = {v: _by_id(scoped[v]) for v in visits}
        states = {v: _state(p, v, all_records) for v in visits}
        trials = _trial_rows(root, p, by_id)
        tables["trials"].extend(trials)
        tables["endpoints"].extend(_endpoint_rows(root, p, trials, states, by_id))
        tables["visit-status"].extend(_status_rows(root, p, trials, states, by_person, scoped))
        tables["discrepancies"].extend(_discrepancy_rows(root, p))
        if p.refs is not None and p.reports:
            order = VISITS[p.study]  # type: ignore[index]
            reconciled = [p.held[v] for v in order if v in p.reports]
            reports = {f"{p.person_id}-{v}": r for v, r in p.reports.items()}
            tables["exposure-cumulative"].extend(
                person_rows(root.data_kind, p.refs, reconciled, reports)
            )
    tables["enrollment"] = _enrollment_rows(root)
    return tables


# ---------------------------------------------------------------------------------------
# Writing


def _area_inputs(root: DataRoot) -> list[dict[str, Any]]:
    """Every file of ``raw/`` and ``inputs/`` (never ``keys/``), sorted."""
    out = []
    for area in ("raw", "inputs"):
        folder = root.area(area)
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                data = read_bytes(path)
                out.append(
                    {
                        "path": path.relative_to(root.path).as_posix(),
                        "bytes": len(data),
                        "sha256": sha256_bytes(data),
                    }
                )
    return sorted(out, key=lambda e: e["path"])


def _manifest(
    root: DataRoot, area: str, files: Mapping[str, bytes], inputs: list[dict[str, Any]]
) -> bytes:
    doc = {
        "format": OUTPUTS_MANIFEST_FORMAT,
        "format_version": OUTPUTS_MANIFEST_FORMAT_VERSION,
        "data_kind": root.data_kind,
        "area": area,
        "analyzer": {"name": "av-analysis", "version": __version__},
        "seeds": [],
        "inputs": inputs,
        "files": [
            {"path": name, "bytes": len(data), "sha256": sha256_bytes(data)}
            for name, data in sorted(files.items())
        ],
    }
    errors = list(validator("outputs-manifest.schema.json").iter_errors(doc))
    if errors:  # pragma: no cover - a defect of this module
        raise AssertionError(errors[0].message)
    return json_bytes(doc)


def write_tables(root: DataRoot, tables: dict[str, list[Row]]) -> list[Path]:
    """Write the tables and the two area manifests; returns the paths written."""
    written: list[Path] = []
    by_area: dict[str, dict[str, bytes]] = {"reconciled": {}, "derived": {}}
    for name, spec in TABLES.items():
        data = table_bytes(spec, tables.get(name, []), root.data_kind)
        written.append(write_output(root, spec.area, spec.filename, data, root.data_kind))
        by_area[spec.area][spec.filename] = data
    reconciled = root.area("reconciled")
    for path in sorted(reconciled.glob(f"*/{RECONCILIATION_REPORT}")):
        by_area["reconciled"][path.relative_to(reconciled).as_posix()] = read_bytes(path)
    inputs = _area_inputs(root)
    derived_inputs = inputs + [
        {"path": f"reconciled/{name}", "bytes": len(data), "sha256": sha256_bytes(data)}
        for name, data in sorted(by_area["reconciled"].items())
        if name.endswith(RECONCILIATION_REPORT)
    ]
    for area, files in by_area.items():
        doc = _manifest(root, area, files, inputs if area == "reconciled" else derived_inputs)
        written.append(write_output(root, area, OUTPUTS_MANIFEST, doc, root.data_kind))  # type: ignore[arg-type]
    return written


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Arguments of ``av-analysis derive``."""
    parser.add_argument("--root", required=True, help="data root (av-data-root.json)")


def main(args: argparse.Namespace) -> int:
    """Run ``av-analysis derive``: exit 0 after writing, 2 for a refused input (not a data
    root, a report older than its inputs, data of the other kind)."""
    try:
        root = DataRoot.open(Path(args.root))
        tables = derive_tables(root)
        write_tables(root, tables)
    except (RefusedInputError, WatermarkError, ValueError, OSError) as exc:
        print(f"derive: refusing: {exc}", file=sys.stderr)
        return 2
    counts = ", ".join(f"{name} {len(rows)}" for name, rows in tables.items())
    print(f"derive: wrote {counts}")
    return 0
