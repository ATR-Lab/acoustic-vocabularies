"""Cumulative per-person exposure ledger (#33).

Folds the exposure ledgers of a person's visits, in visit order, into one row per item
(``derived.EXPOSURE_CUMULATIVE``): first exposure that consumed it (audible, estimated or
uncertain), play counts by phase, the scheduled novel visit of held-out messages and the
C4 codes raised for the item. The same fold (:func:`fold`) gives each trial row its
``prior_phrase_exposures``, ``prior_atom_exposures`` and ``novelty`` (``derived.TRIALS``)
and gives check C4 the first audible exposure of every held-out message. Uncertain onset
counts as consumed; a retry never renews novelty.

Order rule: within a visit, plays are taken trial by trial in trial-log order (a play is
linked to its trial by the exposure-ledger extension column ``trial_ref``); a trial's
prior counts are the consuming plays before its own first play. Plays that name no
logged trial count after the visit's last trial. Counts by phase: selection (profile and
atom menus), teaching (lessons) and test (pre-old, protected and validity blocks), each
counting plays that consumed exposure; menu candidates count as plays of the atom whose
meaning the menu shows.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from av_schedules.matrix import cells

from .derived import Row
from .loaders import LoadedTable, RawVisit, load_raw_visit
from .paths import DataRoot, parse_visit_id
from .references import References, cue_of, heldout_visits, load_references, schedule_items
from .vocab import CONSUMING_AUDIBLE_STATUS, VISITS

CONSUMING: Final = frozenset(CONSUMING_AUDIBLE_STATUS)
PHASE_OF_STAGE: Final[dict[str, str]] = {
    "profile_menu": "selection",
    "atom_menu": "selection",
    "atomic_lesson": "teaching",
    "message_lesson": "teaching",
    "pre_old": "test",
    "trained": "test",
    "novel": "test",
    "atomic": "test",
    "no_cue": "test",
    "speech": "test",
    "practice": "other",
}
SELECTION_STAGES: Final = ("profile_menu", "atom_menu")
_MESSAGE: Final = re.compile(r"^([KQ])-a([1-4])-r([1-4])$")
_ATOM: Final = re.compile(r"^[KQ]-[ar][1-4]$")
HELDOUT_MESSAGES: Final = frozenset(c.message_id for c in cells() if c.heldout_set is not None)
C4_CODES: Final = (
    "HOLDOUT_OUTSIDE_TEST",
    "HOLDOUT_WRONG_VISIT",
    "HOLDOUT_REPEAT_AS_NOVEL",
    "UNCERTAIN_NOT_CONSUMED",
    "RETRY_LINK_BROKEN",
    "ANSWER_DISPLAY_LEAK",
    "PLAYBACK_STATUS_CONFLICT",
)


def components(item: str) -> tuple[str, ...]:
    """Atoms of a message ID (action, referent), the atom itself, or () otherwise."""
    m = _MESSAGE.fullmatch(item)
    if m:
        return (f"{m[1]}-a{m[2]}", f"{m[1]}-r{m[3]}")
    return (item,) if _ATOM.fullmatch(item) else ()


def is_message(item: str) -> bool:
    return _MESSAGE.fullmatch(item) is not None


def is_atom(item: str) -> bool:
    return _ATOM.fullmatch(item) is not None


@dataclass(frozen=True)
class VisitLogs:
    """The rows of one held visit that the fold and the checks use."""

    visit_id: str
    person: str
    visit: str
    raw: RawVisit
    trials: tuple[Mapping[str, str], ...]
    plays: tuple[Mapping[str, str], ...]
    sheet: tuple[Mapping[str, str], ...]
    deviations: tuple[Mapping[str, str], ...]
    linked: bool  # the exposure ledger has the trial_ref column

    def plays_by_trial(self) -> dict[str, list[Mapping[str, str]]]:
        out: dict[str, list[Mapping[str, str]]] = {}
        for row in self.plays:
            out.setdefault(row.get("trial_ref", ""), []).append(row)
        return out


def _rows(table: LoadedTable | None) -> tuple[Mapping[str, str], ...]:
    return table.rows if table is not None else ()


def visit_logs(raw: RawVisit) -> VisitLogs:
    """:class:`VisitLogs` of a loaded raw visit (unreadable tables give no rows)."""
    person, visit = parse_visit_id(raw.visit_id)
    ledger = raw.exposure_ledger
    linked = ledger is not None and (not ledger.rows or "trial_ref" in ledger.rows[0])
    if ledger is not None and not ledger.rows and ledger.problems:
        linked = False
    return VisitLogs(
        visit_id=raw.visit_id,
        person=person,
        visit=visit,
        raw=raw,
        trials=_rows(raw.trial_log),
        plays=_rows(raw.exposure_ledger),
        sheet=_rows(raw.run_sheet),
        deviations=_rows(raw.deviations),
        linked=linked,
    )


@dataclass(frozen=True)
class Play:
    """One exposure-ledger row as the fold sees it."""

    visit: str
    event_id: str
    trial_ref: str
    stage: str
    item: str
    whole_phrase: bool
    status: str

    @property
    def consumed(self) -> bool:
        return self.status in CONSUMING


def _play(visit: str, row: Mapping[str, str]) -> Play:
    return Play(
        visit=visit,
        event_id=row.get("event_id", ""),
        trial_ref=row.get("trial_ref", ""),
        stage=row.get("stage", ""),
        item=row.get("atom_or_message_id", ""),
        whole_phrase=row.get("whole_phrase") == "true",
        status=row.get("audible_status", ""),
    )


@dataclass
class ItemState:
    """Running totals of one item (atom or complete message)."""

    first: Play | None = None
    audible: int = 0
    by_phase: dict[str, int] = field(default_factory=dict)
    last_visit: str | None = None


@dataclass(frozen=True)
class Prior:
    """Consuming plays before a trial's first play."""

    phrase: int  # of the trial's complete message (0 for other cues)
    atom: int  # isolated plays of the cue's component atoms (or of the atom itself)


@dataclass
class Fold:
    """Result of folding a person's visits."""

    prior: dict[tuple[str, str], Prior] = field(default_factory=dict)  # (visit, trial_id)
    items: dict[str, ItemState] = field(default_factory=dict)
    plays: list[Play] = field(default_factory=list)  # in fold order

    def item(self, item: str) -> ItemState:
        return self.items.setdefault(item, ItemState())


def _cue(trial: Mapping[str, str]) -> str:
    return trial.get("message_id", "")


def _ordered(visit: VisitLogs) -> list[tuple[Mapping[str, str] | None, list[Play]]]:
    """(trial row or None, its plays) in fold order."""
    by_trial: dict[str, list[Play]] = {}
    for row in visit.plays:
        p = _play(visit.visit, row)
        by_trial.setdefault(p.trial_ref, []).append(p)
    out: list[tuple[Mapping[str, str] | None, list[Play]]] = []
    seen: set[str] = set()
    for trial in visit.trials:
        tid = trial.get("trial_id", "")
        plays = by_trial.get(tid, []) if tid not in seen else []
        seen.add(tid)
        out.append((trial, plays))
    leftover = [p for ref, plays in by_trial.items() if ref not in seen for p in plays]
    if leftover:
        out.append((None, sorted(leftover, key=lambda p: _index(visit, p.event_id))))
    return out


def _index(visit: VisitLogs, event_id: str) -> int:
    for i, row in enumerate(visit.plays):
        if row.get("event_id") == event_id:
            return i
    return len(visit.plays)


def fold(visits: Sequence[VisitLogs]) -> Fold:
    """Fold a person's held visits (in visit order) into prior counts and item states."""
    out = Fold()
    phrase: dict[str, int] = {}
    atom: dict[str, int] = {}
    for visit in visits:
        for trial, plays in _ordered(visit):
            if trial is not None:
                cue = _cue(trial)
                prior = Prior(
                    phrase=phrase.get(cue, 0) if is_message(cue) else 0,
                    atom=sum(atom.get(a, 0) for a in components(cue)),
                )
                out.prior[(visit.visit, trial.get("trial_id", ""))] = prior
            for p in plays:
                out.plays.append(p)
                if not p.item or not p.consumed:
                    continue
                state = out.item(p.item)
                if state.first is None:
                    state.first = p
                state.audible += 1
                phase = PHASE_OF_STAGE.get(p.stage, "other")
                state.by_phase[phase] = state.by_phase.get(phase, 0) + 1
                state.last_visit = p.visit
                if p.whole_phrase:
                    phrase[p.item] = phrase.get(p.item, 0) + 1
                else:
                    atom[p.item] = atom.get(p.item, 0) + 1
    return out


def first_consumptions(result: Fold) -> dict[str, Play]:
    """Complete message -> its first consuming play."""
    return {
        item: state.first
        for item, state in result.items.items()
        if state.first is not None and is_message(item)
    }


# ---------------------------------------------------------------------------------------
# The exposure-cumulative table


def item_lookup(visit: VisitLogs, refs: References | None) -> dict[str, str]:
    """Row name (trial_id or event_id) -> item ID, for mapping discrepancy rows."""
    out: dict[str, str] = {}
    if refs is not None and visit.visit in refs.schedules:
        for _, item in schedule_items(refs.schedules[visit.visit]):
            out[item["trial_id"]] = cue_of(item)
    for row in visit.trials:
        out.setdefault(row.get("trial_id", ""), row.get("message_id", ""))
    for row in visit.plays:
        out[row.get("event_id", "")] = row.get("atom_or_message_id", "")
    return out


def person_rows(
    data_kind: str,
    refs: References,
    visits: Sequence[VisitLogs],
    reports: Mapping[str, Mapping[str, Any]],
) -> list[Row]:
    """Exposure-cumulative rows of one person from their reconciled visits (in order)."""
    result = fold(visits)
    scheduled: set[str] = set()
    order = VISITS[refs.study]  # type: ignore[index]
    last = visits[-1].visit if visits else None
    for v, doc in refs.schedules.items():
        if last is not None and order.index(v) <= order.index(last):
            for _, entry in schedule_items(doc):
                cue = cue_of(entry)
                if is_message(cue) or is_atom(cue):
                    scheduled.add(cue)
    played = {p.item for p in result.plays if is_message(p.item) or is_atom(p.item)}
    novel_visit = heldout_visits(refs)
    violations: dict[str, set[str]] = {}
    for visit in visits:
        lookup = item_lookup(visit, refs)
        for d in reports.get(visit.visit_id, {}).get("discrepancies", []):
            if d["code"] not in C4_CODES:
                continue
            for name in d["rows"]:
                item = lookup.get(name, "")
                if is_message(item) or is_atom(item):
                    violations.setdefault(item, set()).add(d["code"])
    rows: list[Row] = []
    for item in sorted(scheduled | played):
        state = result.items.get(item, ItemState())
        kind = "message" if is_message(item) else "atom"
        status = (
            "atom" if kind == "atom" else ("heldout" if item in HELDOUT_MESSAGES else "trained")
        )
        first = state.first
        rows.append(
            {
                "data_kind": data_kind,
                "study": refs.study,
                "set": refs.set_name,
                "unit_id": refs.unit_id,
                "person_id": refs.person_id,
                "item_id": item,
                "item_kind": kind,
                "matrix_status": status,
                "scheduled_novel_visit": novel_visit.get(item) if status == "heldout" else None,
                "first_audible_visit": first.visit if first else None,
                "first_audible_event_id": first.event_id if first and first.event_id else None,
                "first_audible_status": first.status if first else None,
                "first_audible_block": _block_of(first.stage) if first else None,
                "audible_plays_n": state.audible,
                "selection_plays_n": state.by_phase.get("selection", 0),
                "teaching_plays_n": state.by_phase.get("teaching", 0),
                "test_plays_n": state.by_phase.get("test", 0),
                "last_visit": last,
                "violations": tuple(c for c in C4_CODES if c in violations.get(item, set())),
            }
        )
    return rows


_BLOCK_OF_STAGE: Final[dict[str, str]] = {
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


def _block_of(stage: str) -> str | None:
    return _BLOCK_OF_STAGE.get(stage)


def group_by_person(visit_ids: Iterable[str]) -> dict[str, list[str]]:
    """Person slot -> its visit IDs in visit order."""
    out: dict[str, list[str]] = {}
    for vid in visit_ids:
        person, _ = parse_visit_id(vid)
        out.setdefault(person, []).append(vid)
    for person, vids in out.items():
        order = VISITS[person[0]]  # type: ignore[index]
        vids.sort(key=lambda v: order.index(parse_visit_id(v)[1]))
    return dict(sorted(out.items()))


def build_ledger(root: DataRoot, visit_ids: Sequence[str]) -> list[Row]:
    """Rows of ``exposure-cumulative`` for the given reconciled visits."""
    import json

    from .paths import RECONCILIATION_REPORT

    rows: list[Row] = []
    for _person, vids in group_by_person(visit_ids).items():
        refs = load_references(root, vids[-1])
        visits = [visit_logs(load_raw_visit(root, v)) for v in vids]
        reports: dict[str, Mapping[str, Any]] = {}
        for v in vids:
            path = root.output_path("reconciled", f"{v}/{RECONCILIATION_REPORT}")
            if path.is_file():
                reports[v] = json.loads(path.read_text(encoding="utf-8"))
        rows.extend(person_rows(root.data_kind, refs, visits, reports))
    return rows
