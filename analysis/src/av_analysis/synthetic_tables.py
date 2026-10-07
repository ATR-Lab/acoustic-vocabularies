"""Synthetic derived tables for the analysis pipeline (#34): the table path of ``simulate``.

Turns one dataset's draws (``simulate.draw_a`` / ``simulate.draw_b``) into ``derived``
``trials`` and ``endpoints`` rows that follow the #33 contract (``derived.TRIALS``,
``derived.ENDPOINTS``), using the real schedule structure: allocation lists, visit
schedules, block orders, private targets and semantic permutations all come from
``av_schedules`` with the DEMO seed as master seed. Every schedule item of a held visit
becomes a row (menus and lessons included), so the tables have the size of real data
(about 40,000 trial rows per full-size study).

Generated patterns (all synthetic, labelled ``SYNTHETIC``, documented in
``analysis/docs/pipeline.md``):

* Primary battery (A D0 trained, B W1 trained): the draws' correct/incorrect outcomes and
  faults, so the pipeline reproduces the fast path's primary estimate exactly.
* Other test blocks: correct with ``expit`` of the person-item latent value plus a visit
  shift (trained and pre-old messages), of the person's mean latent value shifted for
  novel messages and atoms, 1/32 for no-cue trials and 0.95 for speech trials.
* Errors: wrong referent, wrong action, both, ``dont_know``, ``timeout``; commits within
  the response window (log-normal response times).
* Faults: lost opportunities (``row_source`` deviation, ``OPPORTUNITY_LOST``), missing
  playback (no onset, no response; a novel trial is retried and the retry stays
  ``first``), audio underrun (uncertain onset) and presentation freeze (both with a
  logged response that scores 0).
* Missingness: withdrawal before or during the primary battery (Study A: no D0 data or a
  partial battery; Study B: withdrawal from V2, V3 or W1 on, or a missed W1), late visits
  (outside the window), missed later visits.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import cache
from typing import Any, Final

import numpy as np
from av_schedules.assign import AAllocation, BAllocation, build_a_allocation, build_b_allocation
from av_schedules.assign_output import a_files, b_files
from av_schedules.design import SetName, Unit, build_units
from av_schedules.matrix import LABELS, trained_cells
from av_schedules.orders import build_visit_schedule
from av_schedules.seeds import MasterSeed, demo_seed
from scipy.special import expit

from .derived import Row
from .simulate import ADraw, BDraw, Scenario, SyntheticDataset, dataset_rng, draw_a, draw_b
from .vocab import (
    ATOMIC_RESPONSE_WINDOW_MS,
    BATTERIES,
    CANONICAL_FAULT_CODES,
    LOST_OPPORTUNITY_CODE,
    RESPONSE_WINDOW_MS,
    VISITS,
    fault_type,
)
from .windows import Window, classify, window

BASE_DATE: Final = date(2027, 3, 1)
VISIT_SHIFT: Final[dict[str, float]] = {
    "D0": 0.0,
    "D7": -0.35,
    "V1": 0.3,
    "V2": 0.2,
    "V3": 0.1,
    "W1": 0.0,
    "W4": -0.4,
}
NOVEL_SHIFT: Final = -1.0
ATOMIC_SHIFT: Final = 0.6
NO_CUE_P: Final = 1.0 / 32.0
SPEECH_P: Final = 0.95
FAULT_KINDS: Final[tuple[tuple[str, float], ...]] = (
    ("missing_playback", 0.5),
    ("audio_underrun", 0.3),
    ("presentation_freeze", 0.2),
)
MESSAGE_ERRORS: Final[tuple[tuple[str, float], ...]] = (
    ("referent", 0.40),
    ("action", 0.30),
    ("both", 0.15),
    ("dont_know", 0.10),
    ("timeout", 0.05),
)
ATOM_ERRORS: Final[tuple[tuple[str, float], ...]] = (
    ("wrong", 0.85),
    ("dont_know", 0.10),
    ("timeout", 0.05),
)
TEST_TYPES: Final = ("pre_old", "trained", "novel", "atomic", "no_cue", "speech")
_TRAINED_INDEX: Final[dict[str, int]] = {c.message_id: i for i, c in enumerate(trained_cells())}


@cache
def a_allocation(seed: str, set_name: str) -> AAllocation:
    """Study A allocation of a DEMO seed (slot list and book key)."""
    return build_a_allocation(demo_seed(seed), _set(set_name))


@cache
def b_allocation(seed: str, set_name: str) -> BAllocation:
    """Study B allocation of a DEMO seed (dyad list)."""
    return build_b_allocation(demo_seed(seed), _set(set_name))


def _set(name: str) -> SetName:
    if name not in ("pilot", "confirmatory"):
        raise ValueError(f"unknown set {name!r}")
    return name  # type: ignore[return-value]


@cache
def _units(seed: str, study: str, set_name: str) -> dict[str, Unit]:
    return {u.unit_id: u for u in build_units(demo_seed(seed), study, _set(set_name))}  # type: ignore[arg-type]


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"expected an integer, got {value!r}")
    return value


def _pick(gen: np.random.Generator, table: Sequence[tuple[str, float]]) -> str:
    u = float(gen.random())
    acc = 0.0
    for name, p in table:
        acc += p
        if u < acc:
            return name
    return table[-1][0]


@dataclass
class _VisitPlan:
    visit: str
    held: bool
    date: date | None
    reason: str | None = None  # missing reason when not held
    stop_block: str | None = None  # battery interrupted by withdrawal
    stop_after: int = 0  # opportunities accounted in it before withdrawal


@dataclass
class _Person:
    """Everything the row writer needs about one person slot."""

    study: str
    set_name: str
    unit: Unit
    person_id: str
    book_id: str
    visits: list[_VisitPlan]
    primary_y: np.ndarray  # (18, 2) bool
    primary_fault: np.ndarray  # (18, 2) bool
    z: np.ndarray  # (18,) latent per trained message
    level: float  # mean latent value
    phrase_plays: dict[str, int] = field(default_factory=dict)
    atom_plays: dict[str, int] = field(default_factory=dict)
    last_play: dict[str, datetime] = field(default_factory=dict)


class _Writer:
    """Builds the rows of one dataset (deterministic given the generator)."""

    def __init__(
        self, scenario: Scenario, seed: str, master: MasterSeed, gen: np.random.Generator
    ) -> None:
        self.scenario = scenario
        self.seed = seed
        self.master = master
        self.gen = gen
        self.trials: list[Row] = []
        self.endpoints: list[Row] = []
        self.deviation_seq = 0
        self.perm_sha: dict[str, str] = {}

    # -- outcomes ----------------------------------------------------------------------

    def _fault_kind(self) -> str:
        if float(self.gen.random()) < self.scenario.lost_share:
            return "lost"
        return _pick(self.gen, FAULT_KINDS)

    def _outcome(self, p: _Person, visit: str, item: Mapping[str, Any]) -> tuple[bool, str | None]:
        tt = item["trial_type"]
        primary_visit = "D0" if p.study == "A" else "W1"
        if visit == primary_visit and tt == "trained":
            i = _TRAINED_INDEX[item["message_id"]]
            r = int(item["pass"]) - 1
            fault = bool(p.primary_fault[i, r])
            return bool(p.primary_y[i, r]), self._fault_kind() if fault else None
        if tt in ("trained", "pre_old"):
            prob = float(expit(p.z[_TRAINED_INDEX[item["message_id"]]] + VISIT_SHIFT[visit]))
        elif tt == "novel":
            prob = float(expit(p.level + NOVEL_SHIFT + VISIT_SHIFT[visit]))
        elif tt == "atomic":
            prob = float(expit(p.level + ATOMIC_SHIFT + VISIT_SHIFT[visit]))
        elif tt == "no_cue":
            prob = NO_CUE_P
        else:
            prob = SPEECH_P
        correct = float(self.gen.random()) < prob
        fault = float(self.gen.random()) < self.scenario.fault_rate
        return correct, self._fault_kind() if fault else None

    # -- rows --------------------------------------------------------------------------

    def _base(self, p: _Person, plan: _VisitPlan, seq: int, anchor_date: date | None) -> Row:
        w = window(p.study, plan.visit)
        days = None
        if w is not None and plan.date is not None and anchor_date is not None:
            days = (plan.date - anchor_date).days
        return {
            "data_kind": "SYNTHETIC",
            "study": p.study,
            "set": p.set_name,
            "unit_id": p.unit.unit_id,
            "book_id": p.book_id,
            "person_id": p.person_id,
            "visit": plan.visit,
            "visit_seq": seq,
            "visit_id": f"{p.person_id}-{plan.visit}",
            "visit_date": None if plan.date is None else plan.date.isoformat(),
            "days_since_anchor": days,
            "timing": classify(p.study, plan.visit, plan.date, anchor_date),
        }

    def _labels(self, item: Mapping[str, Any]) -> tuple[str | None, str | None, str | None]:
        """(family, target action, target referent) of a schedule item."""
        intended = item.get("intended")
        if item["trial_type"] in ("profile_menu", "atom_menu") or intended is None:
            family = None if intended is None else intended["family"]
            return family, None, None
        if intended["kind"] == "message":
            return intended["family"], intended["semantic_action"], intended["semantic_referent"]
        if intended["role"] == "action":
            return intended["family"], intended["semantic_label"], None
        return intended["family"], None, intended["semantic_label"]

    def _respond(
        self,
        row: Row,
        correct: bool,
        family: str,
        target_action: str | None,
        target_referent: str | None,
        onset: int | None,
        window_ms: int,
    ) -> None:
        atom = (target_action is None) != (target_referent is None)
        kind = "correct" if correct else _pick(self.gen, ATOM_ERRORS if atom else MESSAGE_ERRORS)
        rt = int(min(math.exp(float(self.gen.normal(8.05, 0.35))), window_ms - 300))
        if kind in ("dont_know", "timeout"):
            row["response_code"] = kind
            row["exact_correct"] = False
            row["action_correct"] = False if target_action is not None else None
            row["referent_correct"] = False if target_referent is not None else None
            if kind == "dont_know" and onset is not None:
                row["commit_mono_ms"] = onset + rt
            return
        actions = LABELS[family]["action"]  # type: ignore[index]
        referents = LABELS[family]["referent"]  # type: ignore[index]

        def other(options: Sequence[str], target: str) -> str:
            rest = [o for o in options if o != target]
            return rest[int(self.gen.integers(0, len(rest)))]

        act, ref = target_action, target_referent
        if kind in ("action", "both") or (kind == "wrong" and target_action is not None):
            act = other(actions, target_action) if target_action is not None else None
        if kind in ("referent", "both") or (kind == "wrong" and target_referent is not None):
            ref = other(referents, target_referent) if target_referent is not None else None
        row["response_code"] = "commit"
        row["response_action"] = act
        row["response_target"] = ref
        a_ok = None if target_action is None else act == target_action
        r_ok = None if target_referent is None else ref == target_referent
        row["action_correct"] = a_ok
        row["referent_correct"] = r_ok
        row["exact_correct"] = all(v in (None, True) for v in (a_ok, r_ok))
        if onset is not None:
            row["commit_mono_ms"] = onset + rt
            row["response_time_ms"] = rt
        else:
            row["commit_mono_ms"] = _as_int(row["scheduled_onset_mono_ms"]) + rt

    def _trial(
        self,
        p: _Person,
        base: Row,
        block: Mapping[str, Any],
        item: Mapping[str, Any],
        at: datetime,
        mono: int,
        *,
        retry_of: str | None = None,
    ) -> tuple[Row, bool]:
        """One trials row; returns (row, needs_retry)."""
        tt = item["trial_type"]
        family, t_act, t_ref = self._labels(item)
        message_id = item.get("message_id")
        atom_id = item.get("atom_id")
        speech_id = item.get("speech_id")
        item_id = message_id or atom_id or speech_id
        kind = "message" if message_id else "atom" if atom_id else "speech" if speech_id else "none"
        row: Row = {
            **base,
            "session_id": f"DEMO-S-{base['visit_id']}",
            "trial_id": item["trial_id"] if retry_of is None else f"{retry_of}-R1",
            "retry_of": retry_of,
            "row_source": "logged",
            "block": block["block"],
            "block_position": block["position"],
            "position": item["position"],
            "pass": item["pass"],
            "trial_type": tt,
            "family": family,
            "item_id": item_id,
            "item_kind": kind,
            "trained_status": item["trained_status"],
            "target_action": t_act,
            "target_referent": t_ref,
            "response_code": None,
            "response_action": None,
            "response_target": None,
            "exact_correct": None,
            "action_correct": None,
            "referent_correct": None,
            "scheduled_onset_mono_ms": mono + 1000,
            "audio_onset_estimate_mono_ms": None,
            "onset_uncertainty_ms": None,
            "audio_offset_mono_ms": None,
            "commit_mono_ms": None,
            "response_time_ms": None,
            "playback_status": "observed_complete",
            "fault_codes": (),
            "fault_types": (),
            "valid_delivery": True,
            "exposure_consumed": True,
            "novelty": None,
            "prior_phrase_exposures": 0,
            "prior_atom_exposures": 0,
            "actual_delay_hours": None,
            "deviation_ids": (),
            "discrepancy_codes": (),
        }
        # Exposure history before this row.
        if message_id:
            row["prior_phrase_exposures"] = p.phrase_plays.get(message_id, 0)
            fam, rest = message_id.split("-", 1)
            a, r = rest.split("-")
            atoms = (f"{fam}-{a}", f"{fam}-{r}")
            row["prior_atom_exposures"] = sum(p.atom_plays.get(x, 0) for x in atoms)
        elif atom_id:
            row["prior_atom_exposures"] = p.atom_plays.get(atom_id, 0)
        if item_id in p.last_play:
            hours = (at - p.last_play[item_id]).total_seconds() / 3600.0
            row["actual_delay_hours"] = round(hours, 4)
        if tt == "novel":
            row["novelty"] = "first" if p.phrase_plays.get(message_id or "", 0) == 0 else "repeat"
        onset: int | None = mono + 1000 + int(self.gen.integers(20, 60))
        needs_retry = False
        if tt in TEST_TYPES:
            correct, fault = self._outcome(p, base["visit"], item)  # type: ignore[arg-type]
            if retry_of is not None:
                fault = None
            if tt == "no_cue":
                row["playback_status"] = "not_requested"
                row["exposure_consumed"] = False
                onset = None
            if fault == "lost":
                self.deviation_seq += 1
                row.update(
                    row_source="deviation",
                    scheduled_onset_mono_ms=None,
                    playback_status=None,
                    fault_codes=(LOST_OPPORTUNITY_CODE,),
                    fault_types=(fault_type(LOST_OPPORTUNITY_CODE),),
                    valid_delivery=False,
                    exposure_consumed=False,
                    novelty=None,
                    deviation_ids=(f"DEMO-DEV-{self.deviation_seq:05d}",),
                    actual_delay_hours=None,
                )
                return row, False
            if fault == "missing_playback" and tt != "no_cue":
                code = CANONICAL_FAULT_CODES["missing_playback"]
                row.update(
                    playback_status="confirmed_no_onset",
                    fault_codes=(code,),
                    fault_types=("missing_playback",),
                    valid_delivery=False,
                    exposure_consumed=False,
                )
                return row, tt == "novel"
            window_ms = ATOMIC_RESPONSE_WINDOW_MS if tt == "atomic" else RESPONSE_WINDOW_MS
            if onset is not None:
                row["audio_onset_estimate_mono_ms"] = onset
                row["onset_uncertainty_ms"] = 5
                row["audio_offset_mono_ms"] = onset + (700 if kind == "atom" else 1500)
            assert family is not None
            self._respond(row, correct, family, t_act, t_ref, onset, window_ms)
            if fault in ("audio_underrun", "presentation_freeze"):
                ftype = "audio_underrun" if fault == "audio_underrun" else "presentation_freeze"
                row["fault_codes"] = (CANONICAL_FAULT_CODES[ftype],)  # type: ignore[index]
                row["fault_types"] = (ftype,)
                row["valid_delivery"] = False
                if fault == "audio_underrun":
                    row["playback_status"] = "uncertain"
                    row["onset_uncertainty_ms"] = 250
        else:
            row["audio_onset_estimate_mono_ms"] = onset
            row["onset_uncertainty_ms"] = 5
            row["audio_offset_mono_ms"] = (onset or 0) + 1500
        return row, needs_retry

    def _consume(self, p: _Person, row: Row, plays: int, at: datetime) -> None:
        if not row["exposure_consumed"]:
            return
        item = row["item_id"]
        if not isinstance(item, str):
            return
        if row["item_kind"] == "message":
            p.phrase_plays[item] = p.phrase_plays.get(item, 0) + plays
        elif row["item_kind"] == "atom":
            p.atom_plays[item] = p.atom_plays.get(item, 0) + plays
        p.last_play[item] = at

    def person(self, p: _Person) -> None:
        dates = {v.visit: v.date for v in p.visits}
        for seq, plan in enumerate(p.visits, start=1):
            w = window(p.study, plan.visit)
            anchor_date = dates.get(w.anchor) if w is not None else None
            base = self._base(p, plan, seq, anchor_date)
            doc = None
            if plan.held:
                doc = build_visit_schedule(
                    self.master,
                    p.unit,
                    p.person_id,
                    plan.visit,
                    permutation_sha256=self._perm(p.unit),
                )
            counts = self._visit_rows(p, plan, base, doc)
            self._endpoint_rows(p, plan, base, w, counts)

    def _perm(self, unit: Unit) -> str:
        if unit.unit_id not in self.perm_sha:
            import hashlib

            from av_schedules.curriculum import permutation_json

            self.perm_sha[unit.unit_id] = hashlib.sha256(permutation_json(unit)).hexdigest()
        return self.perm_sha[unit.unit_id]

    def _visit_rows(
        self, p: _Person, plan: _VisitPlan, base: Row, doc: Mapping[str, Any] | None
    ) -> dict[str, dict[str, int]]:
        """Write the visit's rows; returns per-battery counts (scheduled, accounted, ...)."""
        counts: dict[str, dict[str, int]] = {}
        if doc is None:
            from av_schedules.orders import visit_plan

            for bp in visit_plan("A" if p.study == "A" else "B", plan.visit):
                if bp.block in BATTERIES:
                    counts[bp.block] = {"scheduled": bp.count}
            return counts
        assert plan.date is not None
        start = datetime(plan.date.year, plan.date.month, plan.date.day, 9, 0, tzinfo=UTC)
        mono = 5_000_000 + 7_200_000 * _as_int(base["visit_seq"])
        elapsed = 0
        stopped = False
        for block in doc["blocks"]:
            name = block["block"]
            c = {
                "scheduled": len(block["items"]),
                "accounted": 0,
                "fault": 0,
                "lost": 0,
                "valid": 0,
                "retry": 0,
            }
            if name in BATTERIES:
                counts[name] = c
            for item in block["items"]:
                if stopped:
                    break
                at = start + timedelta(seconds=elapsed)
                row, retry = self._trial(p, base, block, item, at, mono + elapsed * 1000)
                self.trials.append(row)
                self._consume(p, row, int(item["plays"]), at)
                if item["trial_type"] in TEST_TYPES:
                    c["accounted"] += 1
                    c["fault"] += int(bool(row["fault_codes"]) or bool(row["fault_types"]))
                    c["lost"] += int(row["row_source"] == "deviation")
                    c["valid"] += int(bool(row["valid_delivery"]))
                elapsed += int(item["slot_s"])
                if retry:
                    at = start + timedelta(seconds=elapsed)
                    rrow, _ = self._trial(
                        p,
                        base,
                        block,
                        item,
                        at,
                        mono + elapsed * 1000,
                        retry_of=str(row["trial_id"]),
                    )
                    self.trials.append(rrow)
                    self._consume(p, rrow, int(item["plays"]), at)
                    c["retry"] += 1
                    elapsed += int(item["slot_s"])
                if plan.stop_block == name and c["accounted"] >= plan.stop_after:
                    stopped = True
            if stopped and name == plan.stop_block:
                c["stopped"] = 1
        return counts

    def _endpoint_rows(
        self,
        p: _Person,
        plan: _VisitPlan,
        base: Row,
        w: Window | None,
        counts: Mapping[str, Mapping[str, int]],
    ) -> None:
        after_stop = False
        for battery in BATTERIES:
            if battery not in counts:
                continue
            c = counts[battery]
            scheduled = c["scheduled"]
            accounted = c.get("accounted", 0)
            status = (
                "complete" if accounted == scheduled else "partial" if accounted > 0 else "missing"
            )
            reason = None
            if status != "complete":
                if not plan.held:
                    reason = plan.reason
                elif c.get("stopped") or battery == plan.stop_block:
                    reason = "withdrawn_mid_battery"
                    after_stop = True
                else:
                    reason = "withdrawn" if after_stop or plan.stop_block else "other"
            timing = base["timing"]
            self.endpoints.append(
                {
                    "data_kind": "SYNTHETIC",
                    "study": p.study,
                    "set": p.set_name,
                    "unit_id": p.unit.unit_id,
                    "book_id": p.book_id,
                    "person_id": p.person_id,
                    "visit": plan.visit,
                    "visit_seq": base["visit_seq"],
                    "visit_id": base["visit_id"],
                    "battery": battery,
                    "scheduled_n": scheduled,
                    "accounted_n": accounted,
                    "fault_n": c.get("fault", 0),
                    "lost_n": c.get("lost", 0),
                    "valid_delivery_n": c.get("valid", 0),
                    "retry_n": c.get("retry", 0),
                    "status": status,
                    "missing_reason": reason,
                    "visit_date": base["visit_date"],
                    "anchor_visit": None if w is None else w.anchor,
                    "days_since_anchor": base["days_since_anchor"],
                    "window_lo_days": None if w is None else w.lo_days,
                    "window_hi_days": None if w is None else w.hi_days,
                    "timing": timing,
                    "planned_endpoint": status == "complete"
                    and timing in ("in_window", "not_applicable"),
                    "reconciliation": "pass" if plan.held else "not_run",
                }
            )


def _a_people(
    scenario: Scenario, seed: str, alloc: AAllocation, draw: ADraw, gen: np.random.Generator
) -> list[_Person]:
    units = _units(seed, "A", scenario.set_name)
    order = {s.slot_id: s.order for s in alloc.slots}
    slot_book = {s.slot_id: s.book_id for s in alloc.slots}
    batches = sorted(b.unit_id for b in alloc.batches)
    people = []
    for u, unit_id in enumerate(batches):
        books = {b.method: b for b in alloc.books if b.unit_id == unit_id}
        for m, method in enumerate(("A1", "A2", "A3")):
            for learner, slot in enumerate(sorted(books[method].slots)):
                d0 = BASE_DATE + timedelta(days=(order[slot] - 1) // 4)
                missing = bool(draw.missing[u, m, learner])
                partial = bool(draw.partial[u, m, learner])
                plans: list[_VisitPlan]
                if missing and not partial:
                    plans = [
                        _VisitPlan("D0", False, None, "withdrawn"),
                        _VisitPlan("D7", False, None, "withdrawn"),
                    ]
                elif partial:
                    plans = [
                        _VisitPlan(
                            "D0",
                            True,
                            d0,
                            stop_block="trained",
                            stop_after=int(draw.cut[u, m, learner]),
                        ),
                        _VisitPlan("D7", False, None, "withdrawn"),
                    ]
                else:
                    u7 = float(gen.random())
                    if u7 < scenario.attrition:
                        d7 = _VisitPlan("D7", False, None, "missed")
                    else:
                        late = float(gen.random()) < scenario.late_rate
                        d7 = _VisitPlan("D7", True, d0 + timedelta(days=9 if late else 7))
                    plans = [_VisitPlan("D0", True, d0), d7]
                people.append(
                    _Person(
                        study="A",
                        set_name=scenario.set_name,
                        unit=units[unit_id],
                        person_id=slot,
                        book_id=slot_book[slot],
                        visits=plans,
                        primary_y=draw.y[u, m, learner],
                        primary_fault=draw.fault[u, m, learner],
                        z=draw.z[u, m, learner],
                        level=float(np.mean(draw.z[u, m, learner])),
                    )
                )
    return people


def _b_people(
    scenario: Scenario, seed: str, alloc: BAllocation, draw: BDraw, gen: np.random.Generator
) -> list[_Person]:
    units = _units(seed, "B", scenario.set_name)
    main = [d for d in alloc.dyads if d.kind == "dyad"]
    people = []
    for k, dyad in enumerate(main):
        v1 = BASE_DATE + timedelta(days=(dyad.order - 1) // 2)
        roles = {m.role: m.slot_id for m in dyad.members}
        for member, role in enumerate(("active", "yoked")):
            missing = bool(draw.missing[k, member])
            partial = bool(draw.partial[k, member])
            late = bool(draw.late[k, member])
            dates = {
                "V1": v1,
                "V2": v1 + timedelta(days=2),
                "V3": v1 + timedelta(days=4),
                "W1": v1 + timedelta(days=4 + (10 if late else 7)),
                "W4": v1 + timedelta(days=4 + 28),
            }
            plans = [_VisitPlan(v, True, dates[v]) for v in VISITS["B"]]
            if missing and partial:
                plans[3] = _VisitPlan(
                    "W1",
                    True,
                    dates["W1"],
                    stop_block="trained",
                    stop_after=int(draw.cut[k, member]),
                )
                plans[4] = _VisitPlan("W4", False, None, "withdrawn")
            elif missing:
                if float(gen.random()) < 0.5:
                    plans[3] = _VisitPlan("W1", False, None, "missed")
                else:
                    first = 1 + int(gen.integers(0, 3))  # withdraw from V2, V3 or W1 on
                    for i in range(first, 5):
                        plans[i] = _VisitPlan(VISITS["B"][i], False, None, "withdrawn")
            if plans[4].held and float(gen.random()) < scenario.attrition / 2:
                plans[4] = _VisitPlan("W4", False, None, "missed")
            slot = roles[role]
            people.append(
                _Person(
                    study="B",
                    set_name=scenario.set_name,
                    unit=units[dyad.unit_id],
                    person_id=slot,
                    book_id=dyad.bank_id,
                    visits=plans,
                    primary_y=draw.y[k, member],
                    primary_fault=draw.fault[k, member],
                    z=draw.z[k, member],
                    level=float(np.mean(draw.z[k, member])),
                )
            )
    return people


def build_dataset(scenario: Scenario, seed: str, index: int = 0) -> SyntheticDataset:
    """Dataset ``index`` of a scenario: derived tables plus the allocation lists."""
    if not seed.startswith("DEMO-"):
        raise ValueError("synthetic datasets need a DEMO- seed label")
    gen = dataset_rng(seed, scenario, index)
    master = demo_seed(seed)
    files: dict[str, bytes] = {}
    if scenario.study == "A":
        alloc = a_allocation(seed, scenario.set_name)
        units = len(alloc.batches)
        per_book = len(alloc.books[0].slots)
        draw = draw_a(scenario, gen, units, per_book)
        people = _a_people(scenario, seed, alloc, draw, gen)
        listed = a_files(alloc)
        files[f"keys/A/{scenario.set_name}-book-key.json"] = listed[
            f"{scenario.set_name}-book-key.json"
        ]
        files[f"inputs/schedules/A/{scenario.set_name}-slots.json"] = listed[
            f"{scenario.set_name}-slots.json"
        ]
    else:
        alloc_b = b_allocation(seed, scenario.set_name)
        families = [c.family for c in trained_cells()]
        structured = np.asarray(
            [
                [f == d.structured_family for f in families]
                for d in alloc_b.dyads
                if d.kind == "dyad"
            ],
            dtype=bool,
        )
        draw_bb = draw_b(scenario, gen, structured)
        people = _b_people(scenario, seed, alloc_b, draw_bb, gen)
        listed = b_files(alloc_b)
        files[f"inputs/schedules/B/{scenario.set_name}-dyads.json"] = listed[
            f"{scenario.set_name}-dyads.json"
        ]
    writer = _Writer(scenario, seed, master, gen)
    for person in sorted(people, key=lambda x: x.person_id):
        writer.person(person)
    return SyntheticDataset(
        scenario=scenario.name,
        seed=seed,
        tables={"trials": writer.trials, "endpoints": writer.endpoints},
        files=files,
        index=index,
        study=scenario.study,
        set_name=scenario.set_name,
        seeds=(f"{seed}:{scenario.name}:dataset:{index}",),
    )
