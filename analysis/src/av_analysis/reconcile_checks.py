"""The rules of checks C1-C8 and the deviation links (#33).

``reconcile`` builds a :class:`Context` for one visit (its raw logs, its reference
inputs, the person's earlier held visits for the exposure history and the windows, the
other dyad member's visit for C6, and the deviation records) and calls :func:`evaluate`.
Each check returns :class:`Found` values (code, rows, generated detail); :func:`link`
then resolves each against the deviation records and C8 reports what stays unresolved.
The full rule list, with the operator guide per code, is in
``analysis/docs/reconciliation.md``.

Details name rows, items and rules, never responses, scores, semantic labels, coded
participant IDs or roles (C6 is written identically into both members' reports).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Final

from av_schedules.orders import PLAYS, atoms_upto, study_visits, visit_wave

from .codes import CHECK_BY_ID, CODE_BY_ID, CheckId
from .ledger import (
    HELDOUT_MESSAGES,
    SELECTION_STAGES,
    VisitLogs,
    components,
    first_consumptions,
    fold,
    is_message,
)
from .loaders import LoadedTable
from .references import (
    ExpectedHash,
    References,
    canonical_sha256,
    combination_key,
    committed,
    cue_of,
    heldout_visits,
    input_path,
    option_key,
    schedule_items,
)
from .schemas import validator
from .templates import TEMPLATES
from .vocab import CONSUMING_AUDIBLE_STATUS, VISITS, CheckStatus, parse_timestamp
from .windows import ANCHOR_VISITS, classify, window, yoked_gap_ok

TEST_TRIAL_TYPES: Final = ("pre_old", "trained", "novel", "atomic", "no_cue", "speech")
NO_PLAY_CUE: Final = ("profile_menu", "no_cue")
YOKED_TIMING_TOLERANCE_MS: Final = 100  # proposal: menu replay onsets within 100 ms
YOKED_FIELDS: Final = (
    "stage",
    "atom_or_message_id",
    "candidate_id",
    "accepted_or_rejected",
    "waveform_sha256",
    "whole_phrase",
    "presentation_index",
    "meaning_display_id",
    "active_choice_or_default",
)
_CANDIDATE: Final = re.compile(r"^([KQ]-[ar][1-4])-([1-4])$")
# Deviation categories that resolve a code when a record names only the visit, the person
# slot or the participant (a record naming the row itself resolves any code).
LINK_CATEGORIES: Final[dict[str, frozenset[str]]] = {
    "RAW_MANIFEST_MISSING": frozenset({"technical", "procedure", "correction"}),
    "RAW_FILE_MISSING": frozenset({"technical", "procedure", "correction"}),
    "RAW_FILE_UNLISTED": frozenset({"technical", "procedure", "correction"}),
    "RAW_HASH_CHANGED": frozenset({"technical", "correction"}),
    "RAW_FORMAT": frozenset({"technical", "procedure", "correction"}),
    "REFERENCE_INPUT": frozenset({"technical", "procedure", "correction"}),
    "COUNT_MISSING_TRIAL": frozenset({"technical", "audio", "withdrawal", "comfort", "procedure"}),
    "COUNT_EXTRA_TRIAL": frozenset({"technical", "audio", "procedure"}),
    "COUNT_MISSING_PLAY": frozenset({"technical", "audio", "procedure"}),
    "COUNT_EXTRA_PLAY": frozenset({"technical", "audio", "procedure"}),
    "COUNT_RUN_SHEET": frozenset({"procedure", "correction"}),
    "BLOCK_ORDER": frozenset({"procedure", "technical"}),
    "WAVEFORM_HASH_MISMATCH": frozenset({"technical", "audio", "correction"}),
    "WAVEFORM_HASH_MISSING": frozenset({"technical", "audio", "correction"}),
    "PACKAGE_HASH_MISMATCH": frozenset({"technical", "correction"}),
    "HOLDOUT_OUTSIDE_TEST": frozenset({"corpus_exposure", "technical", "audio", "procedure"}),
    "HOLDOUT_WRONG_VISIT": frozenset({"corpus_exposure", "technical", "audio", "procedure"}),
    "HOLDOUT_REPEAT_AS_NOVEL": frozenset({"technical", "audio", "correction"}),
    "UNCERTAIN_NOT_CONSUMED": frozenset({"technical", "audio", "correction"}),
    "RETRY_LINK_BROKEN": frozenset({"technical", "audio", "procedure", "correction"}),
    "ANSWER_DISPLAY_LEAK": frozenset({"answer_leak", "technical"}),
    "OLD_ATOM_CHANGED": frozenset({"technical", "correction"}),
    "STORE_CHAIN_BROKEN": frozenset({"technical", "correction"}),
    "YOKED_SOURCE_MISSING": frozenset({"matching"}),
    "YOKED_MISMATCH": frozenset({"matching"}),
    "YOKED_GAP": frozenset({"matching", "window"}),
    "WINDOW_EARLY": frozenset({"window"}),
    "WINDOW_LATE": frozenset({"window"}),
    "VISIT_ORDER": frozenset({"window", "procedure", "missed_visit"}),
    "DEVIATION_UNKNOWN": frozenset({"correction"}),
    "DEVIATION_MISSING": frozenset(),
}
# Deviation categories of a verified apparatus or logger failure: a COUNT_MISSING_TRIAL
# resolved by one of them becomes a ``row_source`` deviation trial row (``derive``).
LOST_OPPORTUNITY_CATEGORIES: Final = frozenset({"technical", "audio"})
WITHDRAWAL_CATEGORIES: Final = frozenset({"withdrawal", "comfort"})


@dataclass(frozen=True)
class Found:
    """A discrepancy before linking: check, code, rows involved, generated detail."""

    check: CheckId
    code: str
    rows: tuple[str, ...]
    detail: str
    pair: bool = False  # C6: deviation scope covers both dyad members


@dataclass(frozen=True)
class Record:
    """One deviation record (a visit's deviations.csv or the study-wide log)."""

    deviation_id: str
    category: str
    event_id: str
    participant_id: str
    resolution: str
    prior_audio_exposure: str
    source: str  # path relative to the data root


def records_of(table: LoadedTable | None) -> list[Record]:
    """Deviation records of a deviations table (rows without an ID are skipped)."""
    if table is None:
        return []
    return [
        Record(
            deviation_id=row.get("deviation_id", ""),
            category=row.get("category", ""),
            event_id=row.get("event_id", ""),
            participant_id=row.get("participant_id", ""),
            resolution=row.get("resolution", ""),
            prior_audio_exposure=row.get("prior_audio_exposure", ""),
            source=table.path,
        )
        for row in table.rows
        if row.get("deviation_id")
    ]


@dataclass
class Context:
    """Everything one visit is checked against."""

    root_synthetic: bool
    this: VisitLogs
    refs: References | None
    reference_error: tuple[str, str] | None  # (path, message) when refs is None
    history: dict[str, VisitLogs]  # earlier held visits of the person
    partner: VisitLogs | None  # Study B V1-V3: the other member's same visit
    partner_id: str | None  # its visit ID (held or not)
    log: LoadedTable | None  # raw/deviations-log.csv
    records: list[Record] = field(default_factory=list)  # this visit + the log
    partner_records: list[Record] = field(default_factory=list)

    @property
    def study(self) -> str:
        return self.this.person[0]


# ---------------------------------------------------------------------------------------
# Shared helpers


def usable(table: LoadedTable | None) -> bool:
    """A table that exists and whose header is valid."""
    return table is not None and not any(p.line == 1 for p in table.problems)


def visit_times(logs: VisitLogs) -> tuple[datetime | None, datetime | None]:
    """First run-sheet start and last end (aware datetimes), None when not recorded."""
    starts: list[datetime] = []
    ends: list[datetime] = []
    for row in logs.sheet:
        for column, out in (("start_time", starts), ("end_time", ends)):
            try:
                if row.get(column):
                    out.append(parse_timestamp(row[column]))
            except ValueError:
                pass
    return (min(starts) if starts else None, max(ends) if ends else None)


def visit_date(logs: VisitLogs | None) -> date | None:
    """Calendar date of the first run-sheet start_time, in its recorded offset."""
    if logs is None:
        return None
    first: datetime | None = None
    for row in logs.sheet:
        try:
            t = parse_timestamp(row.get("start_time", ""))
        except ValueError:
            continue
        if first is None or t < first:
            first = t
    return first.date() if first is not None else None


def _items(refs: References) -> dict[str, tuple[Mapping[str, Any], Mapping[str, Any], int]]:
    """Scheduled trial_id -> (block, item, run index) of the reconciled visit."""
    out: dict[str, tuple[Mapping[str, Any], Mapping[str, Any], int]] = {}
    for i, (block, item) in enumerate(schedule_items(refs.schedules[refs.visit])):
        out[item["trial_id"]] = (block, item, i)
    return out


def _detail(text: str) -> str:
    return text if len(text) <= 300 else text[:297] + "..."


def _sorted_rows(rows: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(r for r in rows if r)))


def concerns(ctx: Context, row: Mapping[str, str]) -> bool:
    """Whether a deviations-log row names this visit (its ID, a row of it, the person slot
    or the coded participant ID)."""
    event = row.get("event_id", "")
    logs = ctx.this
    if event in (logs.visit_id, logs.person) or event.startswith(f"{logs.visit_id}-"):
        return True
    if event and any(p.get("event_id") == event for p in logs.plays):
        return True
    return ctx.refs is not None and row.get("participant_id") == ctx.refs.participant_id


# ---------------------------------------------------------------------------------------
# C1 raw-integrity


def check_c1(ctx: Context) -> list[Found]:
    raw = ctx.this.raw
    out: list[Found] = []

    def add(code: str, rows: Sequence[str], detail: str) -> None:
        out.append(Found("C1", code, tuple(rows), _detail(detail)))

    manifest = raw.exit_manifest
    listed: dict[str, Mapping[str, Any]] = {}
    if "exit-manifest.json" not in raw.files:
        add("RAW_MANIFEST_MISSING", ["exit-manifest.json"], "no exit manifest in the folder")
    elif manifest is None:
        add("RAW_FORMAT", ["exit-manifest.json"], "exit manifest is not a JSON object")
    else:
        errors = sorted(validator("exit-manifest.schema.json").iter_errors(manifest), key=str)
        if errors:
            where = ".".join(str(p) for p in errors[0].absolute_path) or "document"
            add("RAW_FORMAT", ["exit-manifest.json"], f"exit manifest schema violation at {where}")
        if manifest.get("visit_id") != raw.visit_id:
            add("RAW_FORMAT", ["exit-manifest.json"], "exit manifest names another visit")
        for entry in manifest.get("files", []) if isinstance(manifest.get("files"), list) else []:
            if isinstance(entry, Mapping) and isinstance(entry.get("path"), str):
                listed[entry["path"]] = entry
        for name, entry in sorted(listed.items()):
            if name not in raw.files:
                add("RAW_FILE_MISSING", [name], "file listed in the exit manifest is missing")
            elif entry.get("sha256") != raw.files[name] or entry.get("bytes") != raw.sizes[name]:
                add("RAW_HASH_CHANGED", [name], "SHA-256 or size differs from the exit manifest")
        for name in sorted(raw.files):
            if name != "exit-manifest.json" and name not in listed:
                add("RAW_FILE_UNLISTED", [name], "file is not listed in the exit manifest")
    reported = {r for f in out if f.code == "RAW_FILE_MISSING" for r in f.rows}
    for template in TEMPLATES.values():
        if template.raw_name not in raw.files and template.raw_name not in reported:
            add("RAW_FILE_MISSING", [template.raw_name], f"required {template.name} file missing")
    tables = (raw.trial_log, raw.exposure_ledger, raw.run_sheet, raw.deviations)
    for table in (*tables, ctx.log):
        if table is None:
            continue
        name = table.path.rsplit("/", 1)[-1]
        by_line: dict[int, list[str]] = {}
        for p in table.problems:
            by_line.setdefault(p.line, []).append(
                f"{p.column}: {p.message}" if p.column else p.message
            )
        key = {"trial-log": "trial_id", "exposure-ledger": "event_id"}.get(table.template.name)
        for line, messages in sorted(by_line.items()):
            if table is ctx.log and line > 1 and not concerns(ctx, table.rows[line - 2]):
                continue  # a study-wide log line about another visit
            row_name = f"{name}:{line}"
            if line > 1 and key is not None:
                row_name = table.rows[line - 2].get(key) or row_name
            add("RAW_FORMAT", [row_name], f"{name} line {line}: {'; '.join(messages)}")
    out.extend(_c1_identity(ctx))
    if ctx.reference_error is not None:
        add("REFERENCE_INPUT", [ctx.reference_error[0]], ctx.reference_error[1])
    elif ctx.refs is not None:
        for path, message in ctx.refs.problems:
            add("REFERENCE_INPUT", [path], message)
    seen: dict[str, str] = {}
    for record in ctx.records:
        if record.deviation_id in seen and seen[record.deviation_id] != record.source:
            add(
                "RAW_FORMAT",
                [record.deviation_id],
                "deviation ID used in both the visit's deviations and the study-wide log",
            )
        seen[record.deviation_id] = record.source
    return out


def _c1_identity(ctx: Context) -> list[Found]:
    """Identity columns agree with the folder, the exit manifest and the reveal log."""
    logs, refs = ctx.this, ctx.refs
    manifest = logs.raw.exit_manifest or {}
    out: list[Found] = []
    session = manifest.get("session_id")
    expected: dict[str, str] = {"visit": logs.visit}
    if refs is not None:
        expected.update(participant_id=refs.participant_id, study=refs.study)
        if refs.study == "A":
            expected.update(batch_id=refs.unit_id, codebook_id=refs.package_id, dyad_id="")
        else:
            expected.update(dyad_id=refs.unit_id, codebook_id=refs.package_id, batch_id="")
    for table, columns in (
        (
            logs.raw.trial_log,
            (
                "participant_id",
                "study",
                "visit",
                "batch_id",
                "dyad_id",
                "codebook_id",
                "session_id",
            ),
        ),
        (logs.raw.exposure_ledger, ("participant_id", "dyad_id", "session_id")),
    ):
        if not usable(table):
            continue
        assert table is not None
        name = table.path.rsplit("/", 1)[-1]
        wrong = set()
        for row in table.rows:
            for column in columns:
                want = session if column == "session_id" else expected.get(column)
                if isinstance(want, str) and row.get(column, "") != want:
                    wrong.add(column)
        for column in sorted(wrong):
            out.append(
                Found("C1", "RAW_FORMAT", (name,), f"{column} differs from the visit identity")
            )
    if usable(logs.raw.run_sheet):
        allowed = {logs.person} | ({refs.participant_id} if refs is not None else set())
        if any(row.get("participant_id") not in allowed for row in logs.sheet):
            out.append(
                Found(
                    "C1",
                    "RAW_FORMAT",
                    ("visit-run-sheet.csv",),
                    "participant_id is neither the person slot nor the coded ID bound to it",
                )
            )
        if any(row.get("visit") != logs.visit for row in logs.sheet):
            out.append(Found("C1", "RAW_FORMAT", ("visit-run-sheet.csv",), "visit differs"))
        if logs.sheet and visit_date(logs) is None:
            out.append(
                Found("C1", "RAW_FORMAT", ("visit-run-sheet.csv",), "no start_time recorded")
            )
    ledger = logs.raw.exposure_ledger
    if usable(ledger) and not logs.linked:
        out.append(
            Found(
                "C1",
                "RAW_FORMAT",
                ("exposure-ledger.csv",),
                "the trial_ref extension column "
                "is missing: plays cannot be linked to trials (Pending column adapter)",
            )
        )
    return out


# ---------------------------------------------------------------------------------------
# C2 counts


def _longest_increasing(values: Sequence[int]) -> set[int]:
    """Positions of one longest strictly increasing subsequence (patience sorting)."""
    tails: list[int] = []
    tail_pos: list[int] = []
    prev: list[int] = [-1] * len(values)
    for i, v in enumerate(values):
        lo, hi = 0, len(tails)
        while lo < hi:
            mid = (lo + hi) // 2
            if tails[mid] < v:
                lo = mid + 1
            else:
                hi = mid
        if lo == len(tails):
            tails.append(v)
            tail_pos.append(i)
        else:
            tails[lo] = v
            tail_pos[lo] = i
        prev[i] = tail_pos[lo - 1] if lo > 0 else -1
    keep: set[int] = set()
    k = tail_pos[-1] if tail_pos else -1
    while k >= 0:
        keep.add(k)
        k = prev[k]
    return keep


def check_c2(ctx: Context) -> list[Found]:
    refs, logs = ctx.refs, ctx.this
    if refs is None or not usable(logs.raw.trial_log):
        return []
    out: list[Found] = []
    sched = _items(refs)
    plays = logs.plays_by_trial() if usable(logs.raw.exposure_ledger) and logs.linked else None
    logged: dict[str, Mapping[str, str]] = {}
    for row in logs.trials:
        if not row.get("retry_of"):
            logged.setdefault(row.get("trial_id", ""), row)
    for tid, (block, item, _) in sched.items():
        if tid not in logged:
            evidence = [p.get("event_id", "") for p in (plays or {}).get(tid, [])]
            note = f"; {len(evidence)} logged plays name it" if evidence else ""
            out.append(
                Found(
                    "C2",
                    "COUNT_MISSING_TRIAL",
                    (tid, *evidence),
                    f"scheduled trial has no "
                    f"trial-log row (block {block['block']}, position {item['position']}){note}",
                )
            )
    for tid, row in logged.items():
        if tid not in sched:
            out.append(
                Found("C2", "COUNT_EXTRA_TRIAL", (tid,), "trial-log row matches no scheduled item")
            )
            continue
        _, item, _ = sched[tid]
        logged_as = (row.get("trial_type", ""), row.get("message_id", ""))
        scheduled_as = (item["trial_type"], cue_of(item))
        if logged_as != scheduled_as:
            out.append(
                Found(
                    "C2",
                    "BLOCK_ORDER",
                    (tid,),
                    f"trial logged as {logged_as[0]} "
                    f"{logged_as[1] or '(no cue)'}, scheduled {scheduled_as[0]} "
                    f"{scheduled_as[1] or '(no cue)'}",
                )
            )
    # order of the scheduled rows in the file
    order = [
        (row.get("trial_id", ""), sched[row["trial_id"]][2])
        for row in logs.trials
        if not row.get("retry_of") and row.get("trial_id") in sched
    ]
    keep = _longest_increasing([i for _, i in order])
    late: dict[str, list[str]] = {}
    for pos, (tid, _) in enumerate(order):
        if pos not in keep:
            late.setdefault(sched[tid][0]["block"], []).append(tid)
    for block_name, tids in sorted(late.items()):
        out.append(
            Found(
                "C2",
                "BLOCK_ORDER",
                tuple(tids),
                f"{len(tids)} trial(s) of block {block_name} logged outside the scheduled order",
            )
        )
    out.extend(_c2_run_sheet(ctx, sched, logged))
    if plays is not None:
        out.extend(_c2_plays(ctx, sched, plays))
    return out


def _c2_run_sheet(
    ctx: Context,
    sched: Mapping[str, tuple[Mapping[str, Any], Mapping[str, Any], int]],
    logged: Mapping[str, Mapping[str, str]],
) -> list[Found]:
    refs, logs = ctx.refs, ctx.this
    assert refs is not None
    if not usable(logs.raw.run_sheet):
        return []
    out: list[Found] = []
    want = [(r["block"], r["expected_count"]) for r in refs.run_sheet_rows]
    got = [(r.get("block", ""), r.get("expected_count", "")) for r in logs.sheet]
    if want != got:
        out.append(
            Found(
                "C2",
                "COUNT_RUN_SHEET",
                ("visit-run-sheet.csv",),
                "blocks or expected counts differ from the generated run sheet",
            )
        )
    observed: dict[str, int] = {}
    for tid, (sched_block, _, _) in sched.items():
        if tid in logged:
            observed[sched_block["block"]] = observed.get(sched_block["block"], 0) + 1
    previous: datetime | None = None
    for row in logs.sheet:
        block = row.get("block", "")
        name = f"visit-run-sheet.csv:{block}"
        actual = row.get("actual_count", "")
        if actual == "":
            out.append(Found("C2", "COUNT_RUN_SHEET", (name,), "actual_count not recorded"))
        elif actual.lstrip("-").isdigit() and int(actual) != observed.get(block, 0):
            out.append(
                Found(
                    "C2",
                    "COUNT_RUN_SHEET",
                    (name,),
                    f"actual_count {actual} but {observed.get(block, 0)} scheduled trials logged",
                )
            )
        try:
            start = parse_timestamp(row.get("start_time", ""))
        except ValueError:
            continue
        if previous is not None and start < previous:
            out.append(
                Found("C2", "BLOCK_ORDER", (name,), "block started before the previous block")
            )
        previous = start
    return out


def _c2_plays(
    ctx: Context,
    sched: Mapping[str, tuple[Mapping[str, Any], Mapping[str, Any], int]],
    plays: Mapping[str, list[Mapping[str, str]]],
) -> list[Found]:
    logs = ctx.this
    out: list[Found] = []
    trial_ids = {row.get("trial_id", "") for row in logs.trials}
    by_id = {row.get("trial_id", ""): row for row in logs.trials}
    for row in logs.trials:
        tid = row.get("trial_id", "")
        target = row.get("retry_of") or tid
        if target not in sched or (row.get("retry_of") and row.get("retry_of") not in by_id):
            continue  # extra trials and broken retries are reported elsewhere
        _, item, _ = sched[target]
        cue = cue_of(item) if item["trial_type"] not in NO_PLAY_CUE else ""
        expected = PLAYS[item["trial_type"]]
        mine = plays.get(tid, [])
        match = [p for p in mine if p.get("atom_or_message_id", "") == cue]
        other = [p for p in mine if p.get("atom_or_message_id", "") != cue]
        if other:
            out.append(
                Found(
                    "C2",
                    "COUNT_EXTRA_PLAY",
                    (tid, *[p.get("event_id", "") for p in other]),
                    f"{len(other)} play(s) of another item linked to the trial",
                )
            )
        if len(match) > expected:
            extra = [p.get("event_id", "") for p in match[expected:]]
            out.append(
                Found(
                    "C2",
                    "COUNT_EXTRA_PLAY",
                    (tid, *extra),
                    f"{len(match)} audio presentations, {expected} scheduled",
                )
            )
        elif len(match) < expected:
            out.append(
                Found(
                    "C2",
                    "COUNT_MISSING_PLAY",
                    (tid,),
                    f"{len(match)} audio presentations, {expected} scheduled",
                )
            )
    for ref, rows in sorted(plays.items()):
        if ref in trial_ids or ref in sched:
            continue
        for p in rows:
            out.append(
                Found(
                    "C2",
                    "COUNT_EXTRA_PLAY",
                    (p.get("event_id", ""),),
                    "play not linked to a logged or scheduled trial",
                )
            )
    return out


# ---------------------------------------------------------------------------------------
# C3 waveform-hashes


def _expected(
    refs: References, item: str, play: Mapping[str, str] | None, stage: str
) -> list[ExpectedHash]:
    """Expected hashes of a play (or, with ``play`` None, of a trial's first play); an
    empty list when the play cannot be checked (nonsemantic profile examples and speech
    have no package entry, **Pending** #64/#71; Study B without a store snapshot)."""
    if not item:
        return []
    if refs.study == "A":
        e = refs.expected_hashes.get(item)
        return [e] if e is not None else []
    profile, ranks = committed(refs)
    if profile is None:
        return []
    if stage == "atom_menu":
        m = _CANDIDATE.fullmatch(play.get("candidate_id", "")) if play is not None else None
        menu = (int(m[2]),) if m is not None and m[1] == item else (1, 2, 3)
        keys = [option_key(item, profile, rank) for rank in menu]
    else:
        parts = components(item)
        if not parts or any(a not in ranks for a in parts):
            return []
        if is_message(item):
            keys = [combination_key(item, profile, ranks[parts[0]], ranks[parts[1]])]
        else:
            keys = [option_key(item, profile, ranks[item])]
    return [refs.expected_hashes[k] for k in keys if k in refs.expected_hashes]


def _hash_state(row: Mapping[str, str], expected: Sequence[ExpectedHash]) -> str:
    """``ok``, ``missing`` or ``mismatch`` of one logged hash against expected hashes."""
    logged = row.get("waveform_sha256", "")
    if logged:
        return "ok" if any(e.matches(logged) for e in expected) else "mismatch"
    pcm = row.get("pcm_sha256", "")
    composed = all(e.file_sha256 is None for e in expected)
    if composed and pcm:
        return "ok" if any(e.pcm_sha256 == pcm for e in expected) else "mismatch"
    return "missing"


def check_c3(ctx: Context) -> list[Found]:
    refs, logs = ctx.refs, ctx.this
    if refs is None:
        return []
    out: list[Found] = []
    if refs.package_manifest_sha256 != refs.package_sha256:
        rel = input_path("package_manifest", package_id=refs.package_id)
        out.append(
            Found(
                "C3",
                "PACKAGE_HASH_MISMATCH",
                (rel,),
                "package manifest hash differs from the package-hash mapping",
            )
        )
    if usable(logs.raw.run_sheet):
        cell = f"sha256:{refs.package_sha256}"
        if any(row.get("hash_check", "") != cell for row in logs.sheet):
            out.append(
                Found(
                    "C3",
                    "PACKAGE_HASH_MISMATCH",
                    ("visit-run-sheet.csv",),
                    "hash_check differs from the expected package hash",
                )
            )
    if not usable(logs.raw.trial_log):
        return out
    sched = _items(refs)
    plays = logs.plays_by_trial() if usable(logs.raw.exposure_ledger) else {}
    linked = logs.linked
    for row in logs.trials:
        tid = row.get("trial_id", "")
        target = row.get("retry_of") or tid
        if target not in sched:
            continue
        _, item, _ = sched[target]
        if item["trial_type"] in NO_PLAY_CUE or item.get("speech_id"):
            continue
        cue = cue_of(item)
        states: dict[str, list[str]] = {"mismatch": [], "missing": [], "ok": []}
        if row.get("playback_status") != "not_requested":
            expected = _expected(refs, cue, None, item["trial_type"])
            if expected:
                states[_hash_state(row, expected)].append(tid)
        for p in plays.get(tid, []) if linked else []:
            expected = _expected(refs, p.get("atom_or_message_id", ""), p, p.get("stage", ""))
            if expected:
                states[_hash_state(p, expected)].append(p.get("event_id", ""))
        for state, code, what in (
            ("mismatch", "WAVEFORM_HASH_MISMATCH", "matches neither expected hash of the package"),
            (
                "missing",
                "WAVEFORM_HASH_MISSING",
                "has no waveform_sha256 and no PCM hash of composed audio",
            ),
        ):
            if states[state]:
                involved = (tid, *[r for r in states[state] if r != tid])
                out.append(Found("C3", code, involved, f"logged waveform of {cue} {what}"))
    trial_ids = {row.get("trial_id", "") for row in logs.trials}
    for ref, rows in sorted(plays.items()) if linked else []:
        if ref in trial_ids:
            continue
        for p in rows:
            expected = _expected(refs, p.get("atom_or_message_id", ""), p, p.get("stage", ""))
            if expected and _hash_state(p, expected) == "mismatch":
                out.append(
                    Found(
                        "C3",
                        "WAVEFORM_HASH_MISMATCH",
                        (p.get("event_id", ""),),
                        "unlinked play matches neither expected hash of the package",
                    )
                )
    return out


# ---------------------------------------------------------------------------------------
# C4 exposure


def check_c4(ctx: Context) -> list[Found]:
    refs, logs = ctx.refs, ctx.this
    if refs is None or not usable(logs.raw.trial_log):
        return []
    out: list[Found] = []
    order = VISITS[refs.study]  # type: ignore[index]
    history = [ctx.history[v] for v in order if v in ctx.history]
    result = fold([*history, logs])
    scheduled_visit = heldout_visits(refs)
    # held-out complete message outside its novel test
    for p in (pl for pl in result.plays if pl.visit == logs.visit):
        if p.whole_phrase and p.item in HELDOUT_MESSAGES and p.stage != "novel":
            out.append(
                Found(
                    "C4",
                    "HOLDOUT_OUTSIDE_TEST",
                    (p.event_id,),
                    f"complete held-out message {p.item} presented in stage {p.stage}",
                )
            )
    # first audible exposure at another visit than scheduled
    for message, first in sorted(first_consumptions(result).items()):
        if message in HELDOUT_MESSAGES and first.visit == logs.visit:
            due = scheduled_visit.get(message)
            if due != logs.visit:
                out.append(
                    Found(
                        "C4",
                        "HOLDOUT_WRONG_VISIT",
                        (first.event_id,),
                        f"first audible "
                        f"exposure of {message} at {logs.visit}, scheduled "
                        f"{due or 'at no visit'}",
                    )
                )
    plays = logs.plays_by_trial() if logs.linked else {}
    by_id: dict[str, Mapping[str, str]] = {}
    for row in logs.trials:
        by_id.setdefault(row.get("trial_id", ""), row)
    for row in logs.trials:
        tid = row.get("trial_id", "")
        cue = row.get("message_id", "")
        prior = result.prior.get((logs.visit, tid))
        logged_prior = row.get("prior_complete_phrase_exposures", "")
        if (
            row.get("trained_status") == "heldout"
            and prior is not None
            and prior.phrase > 0
            and logged_prior == "0"
        ):
            out.append(
                Found(
                    "C4",
                    "HOLDOUT_REPEAT_AS_NOVEL",
                    (tid,),
                    f"{cue} logged as a first "
                    f"exposure after {prior.phrase} earlier audible play(s)",
                )
            )
        statuses = [p.get("audible_status", "") for p in plays.get(tid, [])]
        heard = row.get("playback_status") in ("observed_complete", "uncertain") or any(
            s in CONSUMING_AUDIBLE_STATUS for s in statuses
        )
        if heard and row.get("exposure_consumed") == "false":
            out.append(
                Found(
                    "C4",
                    "UNCERTAIN_NOT_CONSUMED",
                    (tid,),
                    "audio audible or of uncertain onset but exposure_consumed is false",
                )
            )
        if row.get("trial_type") in TEST_TRIAL_TYPES and (
            row.get("feedback_shown") == "true" or row.get("dictionary_available") == "true"
        ):
            out.append(
                Found(
                    "C4",
                    "ANSWER_DISPLAY_LEAK",
                    (tid,),
                    f"feedback or dictionary available in a {row.get('trial_type')} trial",
                )
            )
    for play_row in logs.plays:
        if play_row.get("stage") in TEST_TRIAL_TYPES and play_row.get("feedback_content_id"):
            out.append(
                Found(
                    "C4",
                    "ANSWER_DISPLAY_LEAK",
                    (play_row.get("event_id", ""),),
                    f"feedback content shown with a play in stage {play_row.get('stage')}",
                )
            )
    out.extend(_c4_retries(ctx, by_id, plays))
    return out


def _c4_retries(
    ctx: Context,
    by_id: Mapping[str, Mapping[str, str]],
    plays: Mapping[str, list[Mapping[str, str]]],
) -> list[Found]:
    logs, refs = ctx.this, ctx.refs
    assert refs is not None
    sched = _items(refs)
    position = {row.get("trial_id", ""): i for i, row in enumerate(logs.trials)}
    retries_of: dict[str, list[str]] = {}
    out: list[Found] = []
    for i, row in enumerate(logs.trials):
        original_id = row.get("retry_of", "")
        if not original_id:
            continue
        tid = row.get("trial_id", "")
        retries_of.setdefault(original_id, []).append(tid)
        original = by_id.get(original_id)
        why = None
        if original is None or original_id not in sched:
            why = "retry_of names no scheduled trial of this visit"
        elif original.get("retry_of"):
            why = "retry_of names a retry (second retry)"
        elif len(retries_of[original_id]) > 1:
            why = "second retry of the same trial"
        elif original.get("playback_status") != "confirmed_no_onset" or any(
            p.get("audible_status") != "confirmed_no_onset" for p in plays.get(original_id, [])
        ):
            why = "the retried trial has no verified no-onset failure"
        else:
            block = sched[original_id][0]["block"]
            later = [
                r
                for r in logs.trials[position[original_id] + 1 : i]
                if r.get("trial_id") in sched and sched[r["trial_id"]][0]["block"] != block
            ]
            same = (row.get("trial_type"), row.get("message_id")) == (
                original.get("trial_type"),
                original.get("message_id"),
            )
            if i < position[original_id] or later or not same:
                why = "the retry is not in the block of the retried trial"
        if why is not None:
            out.append(Found("C4", "RETRY_LINK_BROKEN", _sorted_rows((tid, original_id)), why))
    return out


# ---------------------------------------------------------------------------------------
# C5 growth (Study B)


def check_c5(ctx: Context) -> list[Found]:
    refs = ctx.refs
    if refs is None or refs.study != "B":
        return []
    out: list[Found] = []
    visit = refs.visit
    snapshots = refs.store_snapshots
    receipts = refs.store_receipts
    unit = refs.unit_id
    receipts_rel = input_path("store_receipts", unit_id=unit)

    def snap_rel(v: str) -> str:
        return input_path("store_snapshot", unit_id=unit, visit=v)

    # receipts: self-hashes and the head chain (reported up to this visit's head)
    head_index: dict[str, int] = {}
    chain: list[tuple[int, Found]] = []
    previous: str | None = None
    for n, r in enumerate(receipts, start=1):
        name = f"{receipts_rel.rsplit('/', 1)[-1]}:{n}"
        if r.get("receipt_sha256") != canonical_sha256(r, "receipt_sha256"):
            why = "receipt self-hash differs"
            chain.append((n, Found("C5", "STORE_CHAIN_BROKEN", (name,), why)))
        if r.get("before_head") != previous:
            why = "before_head does not continue the previous receipt's after_head"
            chain.append((n, Found("C5", "STORE_CHAIN_BROKEN", (name,), why)))
        if r.get("status") == "rejected" and r.get("after_head") != r.get("before_head"):
            why = "rejected receipt moved the head"
            chain.append((n, Found("C5", "STORE_CHAIN_BROKEN", (name,), why)))
        after = r.get("after_head")
        previous = after if isinstance(after, str) else None
        if isinstance(after, str):
            head_index[after] = n
    snap = snapshots.get(visit)
    head = snap.get("book_head") if snap is not None else None
    limit = head_index.get(head, len(receipts)) if isinstance(head, str) else len(receipts)
    out.extend(f for n, f in chain if n <= limit)
    if snap is None:
        return out  # C1 reports the missing snapshot
    rel = snap_rel(visit)
    if snap.get("manifest_sha256") != canonical_sha256(snap, "manifest_sha256"):
        out.append(Found("C5", "STORE_CHAIN_BROKEN", (rel,), "snapshot self-hash differs"))
    if not isinstance(head, str) or head not in head_index:
        out.append(
            Found(
                "C5",
                "STORE_CHAIN_BROKEN",
                (rel,),
                "book_head is not the after_head of any selection receipt",
            )
        )
    order = study_visits("B")
    earlier = [v for v in order[: order.index(visit)] if v in snapshots]
    if earlier and isinstance(head, str) and head in head_index:
        prev_head = snapshots[earlier[-1]].get("book_head")
        if isinstance(prev_head, str) and head_index.get(prev_head, 0) > head_index[head]:
            out.append(
                Found(
                    "C5",
                    "STORE_CHAIN_BROKEN",
                    (rel,),
                    f"book_head precedes the head of {earlier[-1]} in the receipt chain",
                )
            )
    if earlier:
        first = snapshots[earlier[0]]
        for key in ("profile", "profile_selection_receipt_sha256"):
            if snap.get(key) != first.get(key):
                out.append(
                    Found("C5", "STORE_CHAIN_BROKEN", (rel,), f"{key} changed since {earlier[0]}")
                )
    entries = {e["atom_id"]: e for e in snap["entries"]}
    committed_at: dict[str, str] = {}
    for v in earlier:
        for e in snapshots[v]["entries"]:
            committed_at.setdefault(e["atom_id"], v)
    for atom_id, v in sorted(committed_at.items()):
        old = next(e for e in snapshots[v]["entries"] if e["atom_id"] == atom_id)
        now = entries.get(atom_id)
        if now is None:
            out.append(
                Found(
                    "C5",
                    "OLD_ATOM_CHANGED",
                    (atom_id,),
                    f"atom committed at {v} is missing from the snapshot",
                )
            )
            continue
        changed = sorted(k for k in old if old[k] != now.get(k))
        if changed:
            out.append(
                Found(
                    "C5",
                    "OLD_ATOM_CHANGED",
                    (atom_id,),
                    f"entry differs from the one committed at {v} ({', '.join(changed)})",
                )
            )
    wave = visit_wave("B", visit)
    receipt_by_hash = {r.get("receipt_sha256"): r for r in receipts}
    lacking = []
    for atom_id in sorted(a for a in atoms_upto(wave) if a not in entries):
        lacking.append(atom_id)
    if lacking:
        out.append(
            Found(
                "C5",
                "STORE_CHAIN_BROKEN",
                tuple(lacking),
                "snapshot lacks atoms introduced by this visit's wave",
            )
        )
    for atom_id, e in sorted(entries.items()):
        if atom_id in committed_at:
            continue
        receipt = receipt_by_hash.get(e.get("selection_receipt_sha256"))
        if (
            receipt is None
            or receipt.get("menu_key") != atom_id
            or receipt.get("rank") != e.get("rank")
            or receipt.get("status") != "committed"
        ):
            out.append(
                Found(
                    "C5",
                    "STORE_CHAIN_BROKEN",
                    (atom_id,),
                    "entry is not backed by a committed selection receipt",
                )
            )
    return out


# ---------------------------------------------------------------------------------------
# C6 yoked-ledger (Study B acquisition visits)


def c6_applies(study: str, visit: str) -> bool:
    return study == "B" and visit in ("V1", "V2", "V3")


def check_c6(ctx: Context) -> list[Found]:
    refs = ctx.refs
    if refs is None or not c6_applies(refs.study, refs.visit):
        return []
    assert ctx.partner_id is not None
    if ctx.partner is None:
        return [
            Found(
                "C6",
                "YOKED_SOURCE_MISSING",
                (ctx.partner_id,),
                "the other member's visit has "
                "no raw logs: the pair's selection events cannot be matched",
                pair=True,
            )
        ]
    pair = {ctx.this.person: ctx.this, ctx.partner.person: ctx.partner}
    active = pair.get(refs.active_person_id or "")
    if active is None:
        return []
    yoked = next(v for p, v in pair.items() if p != active.person)
    out: list[Found] = []

    def add(code: str, rows: Iterable[str], detail: str) -> None:
        out.append(Found("C6", code, _sorted_rows(rows), _detail(detail), pair=True))

    sources = [r for r in active.plays if r.get("stage") in SELECTION_STAGES]
    source_by_id = {r.get("event_id", ""): r for r in sources}
    copies_of: dict[str, list[str]] = {}
    sequence: list[str] = []
    for r in active.plays:
        if r.get("yoked_source_event_id"):
            add(
                "YOKED_MISMATCH",
                [r.get("event_id", "")],
                "a selection event names a source event inconsistently with the pair's ledgers",
            )
    for r in yoked.plays:
        eid = r.get("event_id", "")
        src = r.get("yoked_source_event_id", "")
        if r.get("stage") not in SELECTION_STAGES:
            continue
        if not src or src not in source_by_id:
            add(
                "YOKED_SOURCE_MISSING",
                [eid, src],
                "selection event has no matching source event in the pair's ledgers",
            )
            continue
        copies_of.setdefault(src, []).append(eid)
        sequence.append(src)
        s = source_by_id[src]
        differ = [f for f in YOKED_FIELDS if r.get(f, "") != s.get(f, "")]
        try:
            gap = abs(
                (int(r["audio_onset_mono_ms"]) - int(r["display_start_mono_ms"]))
                - (int(s["audio_onset_mono_ms"]) - int(s["display_start_mono_ms"]))
            )
        except (KeyError, ValueError):
            gap = 0
        if gap > YOKED_TIMING_TOLERANCE_MS:
            differ.append("timing")
        if differ:
            add("YOKED_MISMATCH", [eid, src], f"matched events differ in {', '.join(differ)}")
    for src in source_by_id:
        n = len(copies_of.get(src, []))
        if n == 0:
            add(
                "YOKED_SOURCE_MISSING",
                [src],
                "selection event has no matching counterpart in the pair's ledgers",
            )
        elif n > 1:
            add("YOKED_MISMATCH", [src, *copies_of[src]], "selection event matched more than once")
    expected_order = [s for s in source_by_id if s in copies_of]
    for got, want in zip(list(dict.fromkeys(sequence)), expected_order, strict=False):
        if got != want:
            add("YOKED_MISMATCH", [got, want], "matched selection events are in a different order")
            break
    a_start, a_end = visit_times(active)
    y_start, _ = visit_times(yoked)
    known = a_start is not None and a_end is not None and y_start is not None
    if known and not yoked_gap_ok(a_start, a_end, y_start):  # type: ignore[arg-type]
        add(
            "YOKED_GAP",
            [active.visit_id, yoked.visit_id],
            "the later session did not start "
            "after the earlier session ended and within 24 h of its start",
        )
    return out


# ---------------------------------------------------------------------------------------
# C7 windows


def check_c7(ctx: Context) -> list[Found]:
    logs = ctx.this
    study, visit = ctx.study, logs.visit
    w = window(study, visit)
    if w is None:
        return []
    out: list[Found] = []
    this_date = visit_date(logs)
    anchor = ctx.history.get(w.anchor)
    anchor_date = visit_date(anchor)
    if anchor is None or anchor_date is None:
        out.append(
            Found(
                "C7",
                "VISIT_ORDER",
                (logs.visit_id,),
                f"anchor visit {w.anchor} has no raw logs or no recorded date",
            )
        )
    after = ctx.history.get(w.after)
    after_date = visit_date(after)
    if w.after != w.anchor and (after is None or after_date is None):
        out.append(
            Found(
                "C7",
                "VISIT_ORDER",
                (logs.visit_id,),
                f"{w.after} must precede this visit and has no raw logs or no recorded date",
            )
        )
    if this_date is None:
        return out
    timing = classify(study, visit, this_date, anchor_date)
    if anchor_date is not None and timing in ("early", "late"):
        days = (this_date - anchor_date).days
        code = "WINDOW_EARLY" if timing == "early" else "WINDOW_LATE"
        out.append(
            Found(
                "C7",
                code,
                (logs.visit_id,),
                f"day {days} after {w.anchor}, window {w.lo_days}-{w.hi_days}",
            )
        )
    if after_date is not None and after_date >= this_date:
        out.append(
            Found("C7", "VISIT_ORDER", (logs.visit_id,), f"not on a later date than {w.after}")
        )
    return out


# ---------------------------------------------------------------------------------------
# Deviation links and C8


@dataclass(frozen=True)
class Linked:
    """A discrepancy after linking."""

    found: Found
    deviation_id: str | None
    resolved: bool


def _scope(ctx: Context, pair: bool) -> tuple[set[str], set[str], set[str], list[Record]]:
    """(visit IDs, person slots, coded IDs, records) a discrepancy may be linked through."""
    visits = {ctx.this.visit_id}
    persons = {ctx.this.person}
    coded = {ctx.refs.participant_id} if ctx.refs is not None else set()
    records = list(ctx.records)
    if pair and ctx.partner_id is not None:
        visits.add(ctx.partner_id)
        persons.add(ctx.partner_id.rsplit("-", 1)[0])
        records += ctx.partner_records
        if ctx.partner is not None:
            coded |= {r.get("participant_id", "") for r in ctx.partner.trials[:1]}
    return visits, persons, coded, records


def _explicit(ctx: Context, rows: Sequence[str], pair: bool) -> list[str]:
    logs = [ctx.this, *([ctx.partner] if pair and ctx.partner is not None else [])]
    out: list[str] = []
    wanted = set(rows)
    for v in logs:
        for row in v.trials:
            if row.get("trial_id") in wanted and row.get("deviation_id"):
                out.append(row["deviation_id"])
        for row in v.plays:
            if row.get("event_id") in wanted and row.get("matching_deviation_id"):
                out.append(row["matching_deviation_id"])
    return out


def link(ctx: Context, found: Found) -> Linked:
    """Resolve a discrepancy against the deviation records (see LINK_CATEGORIES)."""
    if found.code == "DEVIATION_MISSING":
        return Linked(found, None, False)
    visits, persons, coded, records = _scope(ctx, found.pair)
    known = {r.deviation_id: r for r in records}
    for dev_id in sorted(set(_explicit(ctx, found.rows, found.pair))):
        if dev_id in known:
            return Linked(found, dev_id, True)
    rows = set(found.rows)
    allowed = LINK_CATEGORIES.get(found.code, frozenset())
    levels: list[list[Record]] = [
        [
            r
            for r in records
            if r.event_id and r.event_id in rows and r.event_id not in visits | persons
        ],
        [r for r in records if r.event_id in visits and r.category in allowed],
        [r for r in records if r.event_id in persons and r.category in allowed],
        [
            r
            for r in records
            if not r.event_id and r.participant_id in coded and r.category in allowed
        ],
    ]
    for level in levels:
        if level:
            best = min(level, key=lambda r: r.deviation_id)
            return Linked(found, best.deviation_id, True)
    return Linked(found, None, False)


def check_c8(ctx: Context, linked: Sequence[Linked]) -> list[Linked]:
    """DEVIATION_UNKNOWN for links to absent records; DEVIATION_MISSING for every
    unresolved discrepancy of C1-C7 (in report order)."""
    known = {r.deviation_id for r in ctx.records}
    unknown: list[Found] = []
    for row in ctx.this.trials:
        dev = row.get("deviation_id", "")
        if dev and dev not in known:
            unknown.append(
                Found(
                    "C8",
                    "DEVIATION_UNKNOWN",
                    (row.get("trial_id", ""),),
                    f"deviation_id {dev} is not in the deviation records",
                )
            )
    for row in ctx.this.plays:
        dev = row.get("matching_deviation_id", "")
        if dev and dev not in known:
            unknown.append(
                Found(
                    "C8",
                    "DEVIATION_UNKNOWN",
                    (row.get("event_id", ""),),
                    f"matching_deviation_id {dev} is not in the deviation records",
                )
            )
    out = [link(ctx, f) for f in unknown]
    for d in linked:
        if not d.resolved:
            out.append(
                Linked(
                    Found(
                        "C8",
                        "DEVIATION_MISSING",
                        d.found.rows,
                        f"unresolved {d.found.code} ({d.found.check}) has no deviation record",
                        pair=d.found.pair,
                    ),
                    None,
                    False,
                )
            )
    return out


CHECKS_IN_ORDER: Final = (
    ("C1", check_c1),
    ("C2", check_c2),
    ("C3", check_c3),
    ("C4", check_c4),
    ("C5", check_c5),
    ("C6", check_c6),
    ("C7", check_c7),
)


def applicable(ctx: Context, check: str) -> bool:
    """Whether a check applies to the visit (and could run)."""
    study, visit = ctx.study, ctx.this.visit
    if study not in CHECK_BY_ID[check].studies:
        return False
    if check == "C6":
        return c6_applies(study, visit)
    if check == "C7":
        return ANCHOR_VISITS[study] != visit
    return not (check in ("C2", "C3", "C4", "C5", "C6") and ctx.refs is None)


def status_of(items: Sequence[Linked], is_applicable: bool) -> CheckStatus:
    if not is_applicable:
        return "not_applicable"
    if not items:
        return "pass"
    return "explained" if all(d.resolved for d in items) else "fail"


_CODE_ORDER: Final = {code: i for i, code in enumerate(CODE_BY_ID)}


def _key(d: Linked) -> tuple[str, tuple[str, ...], int, str]:
    return (d.found.check, d.found.rows, _CODE_ORDER[d.found.code], d.found.detail)


def evaluate(ctx: Context) -> list[tuple[str, CheckStatus, list[Linked]]]:
    """(check, status, linked discrepancies) for C1..C8, discrepancies sorted by rows."""
    results: list[tuple[str, CheckStatus, list[Linked]]] = []
    every: list[Linked] = []
    for check, rule in CHECKS_IN_ORDER:
        ok = applicable(ctx, check)
        found = rule(ctx) if ok else []
        unique = list(dict.fromkeys(found))
        items = sorted((link(ctx, f) for f in unique), key=_key)
        results.append((check, status_of(items, ok), items))
        every.extend(items)
    c8 = sorted(check_c8(ctx, every), key=_key)
    results.append(("C8", status_of(c8, True), c8))
    return results
