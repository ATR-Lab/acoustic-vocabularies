"""Schedule validation suite (#32): every generated schedule, run sheet and list checked.

``build_set`` generates everything for one study and set from a master seed: the units
(#29), every person's visit schedules (#30), the allocation (#31) and the run sheets.
``check_set`` then applies all rules and returns :class:`~av_schedules.findings.Finding`
values naming the unit, person, visit and rule (an empty list means the set passes).
``run_all`` does both. ``design_check_values`` reproduces every numeric field of planning
``design-checks.json`` from generated schedules (not from the matrix constants), and
``assessment_values`` the seconds and counts of ``assessment-schedule.csv``.

Rules (``RULES``): the visit-schedule rules of #30 (``orders.visit_schedule_findings``)
plus history, oracle, allocation and run-sheet rules. Oracles come from
``av_schedules.planning`` and protocol constants, never from the generator's own plan.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from .assign import (
    AAllocation,
    BAllocation,
    build_a_allocation,
    build_b_allocation,
    check_a_allocation,
    check_b_allocation,
)
from .design import B_DEFAULT_SPARES, SET_CODE, SetName, Unit, build_units
from .findings import NA, Finding, format_findings
from .matrix import Study, cells, novel_visit
from .orders import person_ids, study_visits, unit_schedules, visit_schedule_findings
from .planning import (
    ALLOCATION_CHECKS,
    ASSESSMENT_COLUMNS,
    ASSESSMENT_SCHEDULE,
    ATOMIC_SLOT_S,
    B_SCREENING_MINUTES,
    FULL_MESSAGE_SLOT_S,
    PILOT_ALLOCATION_COUNTS,
    booked_minutes,
    design_checks_oracle,
)
from .run_sheets import PackageHashes, hash_cells, run_sheet_csv, run_sheet_findings
from .seeds import MasterSeed

RULES: Final[dict[str, str]] = {
    # visit-schedule rules (#30)
    "blocks": "block sequence, counts and passes equal the visit plan",
    "order": "trained -> novel -> atomic; pre-old before teaching; validity last",
    "once-per-pass": "every item of a two-pass block once per pass",
    "content": "each block holds the expected items",
    "heldout": "no held-out message in lessons, menus, pre-old or the dictionary list",
    "slots": "slot length of every item equals the generator's slot table",
    "seconds": "stored assessment counts and seconds equal the visit plan",
    "validity": "8 no-cue and 8 speech trials covering every action and target once",
    "trial-ids": "trial IDs unique and prefixed by person and visit",
    # #32 rules
    "assessment": "counts and seconds equal assessment-schedule.csv (14 s and 9 s slots)",
    "plays": "scheduled plays: lessons 3, menus 8, test trials 1, no-cue 0",
    "heldout-exposure": "each held-out message played once, in the novel block of its visit",
    "component-availability": "items use only atoms and messages taught by then",
    "training-coverage": "trained messages taught so far use every introduced index",
    "matrix-partition": "trained and held-out messages partition the 32 legal messages",
    "train-novel-overlap": "no message is both trained and tested as novel",
    "design-checks": "per-person and per-set counts equal design-checks.json",
    "booking": "fixed slots fit each visit's booking; B total 260 min",
    "allocation": "allocation count and balance checks (#31)",
    "allocation-coverage": "allocation slots equal schedule persons; swap flags agree",
    "run-sheet": "run sheet equals the template header and the schedule blocks",
    "masking": "no Study A method string in run sheets",
}

# Protocol constants: primary visits (two passes of the 18 trained messages = 36 trials).
PRIMARY_VISITS: Final[dict[str, tuple[str, ...]]] = {"A": ("D0", "D7"), "B": ("W1", "W4")}
# Scheduled audio plays per trial type (Protocol constants; Study B protocol section 5).
PLAYS_ORACLE: Final[dict[str, int]] = {
    "profile_menu": 8,
    "atom_menu": 8,
    "atomic_lesson": 3,
    "message_lesson": 3,
    "pre_old": 1,
    "trained": 1,
    "novel": 1,
    "atomic": 1,
    "no_cue": 0,
    "speech": 1,
}
# Assessment slot per trial type (14 s full-message, 9 s atomic).
TEST_SLOT_S: Final[dict[str, int]] = {
    "pre_old": FULL_MESSAGE_SLOT_S,
    "trained": FULL_MESSAGE_SLOT_S,
    "novel": FULL_MESSAGE_SLOT_S,
    "no_cue": FULL_MESSAGE_SLOT_S,
    "speech": FULL_MESSAGE_SLOT_S,
    "atomic": ATOMIC_SLOT_S,
}
ASSESSMENT_BLOCKS: Final[dict[str, str]] = {
    "pre_old_trained": "pre_old",
    "post_trained": "trained",
    "novel_once": "novel",
    "atomic": "atomic",
    "extra_after_protected": "validity",
}
_MESSAGE: Final = re.compile(r"^([KQ])-a([1-4])-r([1-4])$")
_ATOM: Final = re.compile(r"^([KQ])-([ar])([1-4])$")

Docs = Mapping[str, Mapping[str, Any]]  # visit -> schedule document


@dataclass(frozen=True)
class SetRun:
    """Everything generated for one study and set (input of :func:`check_set`)."""

    master: MasterSeed
    study: Study
    set_name: SetName
    units: tuple[Unit, ...]
    docs: Mapping[str, Docs]  # person -> visit -> schedule document
    unit_of: Mapping[str, str]  # person -> unit ID
    allocation: AAllocation | BAllocation
    hash_cells: Mapping[str, str]  # person -> expected hash_check cell
    run_sheets: Mapping[str, Mapping[str, bytes]]  # person -> visit -> CSV bytes
    package_hashes: PackageHashes | None = None

    @property
    def prefix(self) -> str:
        """Set-level unit label for findings, e.g. ``A-C``."""
        return f"{self.study}-{SET_CODE[self.set_name]}"

    @property
    def main_persons(self) -> tuple[str, ...]:
        kind = {u.unit_id: u.kind for u in self.units}
        return tuple(p for p in self.docs if kind[self.unit_of[p]] != "spare")


def build_set(
    master: MasterSeed,
    study: Study,
    set_name: SetName,
    *,
    spares: int = B_DEFAULT_SPARES,
    package_hashes: PackageHashes | None = None,
) -> SetRun:
    """Generate units, schedules, allocation and run sheets of one study and set."""
    units = build_units(master, study, set_name, spares=spares)
    docs: dict[str, dict[str, Any]] = {}
    unit_of: dict[str, str] = {}
    for u in units:
        built = unit_schedules(master, u)
        for person in person_ids(u):
            unit_of[person] = u.unit_id
            docs[person] = {v: built[f"{person}/{v}.json"] for v in study_visits(study)}
    alloc: AAllocation | BAllocation
    if study == "A":
        alloc = build_a_allocation(master, set_name)
    else:
        alloc = build_b_allocation(master, set_name, spares=spares)
    cells_by_person = hash_cells(master, units, package_hashes)
    sheets = {
        person: {v: run_sheet_csv(doc, cells_by_person[person]) for v, doc in by_visit.items()}
        for person, by_visit in docs.items()
    }
    return SetRun(
        master=master,
        study=study,
        set_name=set_name,
        units=units,
        docs=docs,
        unit_of=unit_of,
        allocation=alloc,
        hash_cells=cells_by_person,
        run_sheets=sheets,
        package_hashes=package_hashes,
    )


def run_all(
    master: MasterSeed,
    study: Study,
    set_name: SetName,
    *,
    spares: int = B_DEFAULT_SPARES,
    package_hashes: PackageHashes | None = None,
) -> list[Finding]:
    """Generate one study and set and run every check (empty list = pass)."""
    return check_set(
        build_set(master, study, set_name, spares=spares, package_hashes=package_hashes)
    )


def check_set(run: SetRun) -> list[Finding]:
    """All findings of one generated set: per visit, per person, per set."""
    out: list[Finding] = []
    for person, by_visit in run.docs.items():
        unit = run.unit_of[person]
        for visit, doc in by_visit.items():
            out += visit_schedule_findings(doc)
            out += assessment_findings(doc)
            sheet = run.run_sheets.get(person, {}).get(visit)
            if sheet is None:
                out.append(Finding(unit, person, visit, "run-sheet", "no run sheet"))
            else:
                out += run_sheet_findings(sheet, doc, run.hash_cells.get(person, ""))
        out += person_findings(run.study, unit, person, by_visit)
    out += set_findings(run)
    return out


# ---------------------------------------------------------------------------------------
# Helpers over schedule documents


def _items(doc: Mapping[str, Any], *blocks: str) -> list[Mapping[str, Any]]:
    return [it for b in doc["blocks"] if not blocks or b["block"] in blocks for it in b["items"]]


def _message_atoms(message_id: str) -> tuple[str, str]:
    m = _MESSAGE.match(message_id)
    if m is None:
        raise ValueError(f"not a message id: {message_id!r}")
    family, a, r = m.groups()
    return f"{family}-a{a}", f"{family}-r{r}"


def _indices(atoms: Iterable[str]) -> dict[tuple[str, str], set[int]]:
    """Introduced indices per (family, role prefix ``a``/``r``)."""
    out: dict[tuple[str, str], set[int]] = {(f, r): set() for f in "KQ" for r in "ar"}
    for atom in atoms:
        m = _ATOM.match(atom)
        if m is not None:
            out[(m.group(1), m.group(2))].add(int(m.group(3)))
    return out


def _legal(atoms: Iterable[str]) -> set[str]:
    """Legal whole messages from taught atoms: every action x referent per family."""
    idx = _indices(atoms)
    return {f"{f}-a{a}-r{r}" for f in "KQ" for a in idx[(f, "a")] for r in idx[(f, "r")]}


def _ids(items: Iterable[Mapping[str, Any]], field: str) -> list[str]:
    return [str(it[field]) for it in items if it.get(field)]


HELDOUT_IDS: Final[frozenset[str]] = frozenset(
    c.message_id for c in cells() if c.heldout_set is not None
)
TRAINED_IDS: Final[frozenset[str]] = frozenset(
    c.message_id for c in cells() if c.heldout_set is None
)


def _heldout_visit(study: Study, message_id: str, swap: bool) -> str:
    cell = next(c for c in cells() if c.message_id == message_id)
    assert cell.heldout_set is not None
    return novel_visit(study, cell.heldout_set, swap)


# ---------------------------------------------------------------------------------------
# Per visit: assessment-schedule.csv oracle


def _oracle_row(study: str, visit: str) -> dict[str, int] | None:
    for row in ASSESSMENT_SCHEDULE:
        if row[0] == study and row[1] == visit:
            return dict(zip(ASSESSMENT_COLUMNS[2:], row[2:], strict=True))
    return None


def visit_assessment(doc: Mapping[str, Any]) -> dict[str, int]:
    """Counts and seconds of one generated schedule, keyed like assessment-schedule.csv.

    ``assessment_seconds`` sums the stored slot of every pre-old and protected item.
    """
    out = {key: len(_items(doc, block)) for key, block in ASSESSMENT_BLOCKS.items()}
    out["assessment_seconds"] = sum(
        int(it["slot_s"]) for it in _items(doc, "pre_old", "trained", "novel", "atomic")
    )
    return out


def assessment_findings(doc: Mapping[str, Any]) -> list[Finding]:
    """Rules ``assessment`` (counts, seconds, 14 s / 9 s slots) and ``plays``."""
    unit, person, visit = str(doc["unit_id"]), str(doc["person_id"]), str(doc["visit"])
    out: list[Finding] = []
    want = _oracle_row(str(doc["study"]), visit)
    if want is None:
        return [Finding(unit, person, visit, "assessment", "visit not in assessment-schedule")]
    got = visit_assessment(doc)
    for key, value in got.items():
        if want[key] != value:
            out.append(
                Finding(unit, person, visit, "assessment", f"{key} = {value}, oracle {want[key]}")
            )
    for it in _items(doc):
        tt = str(it["trial_type"])
        slot = TEST_SLOT_S.get(tt)
        if slot is not None and it["slot_s"] != slot:
            detail = f"{it['trial_id']} {tt} slot {it['slot_s']} s, oracle {slot} s"
            out.append(Finding(unit, person, visit, "assessment", detail))
        if it["plays"] != PLAYS_ORACLE.get(tt):
            detail = f"{it['trial_id']} {tt} plays {it['plays']}, oracle {PLAYS_ORACLE.get(tt)}"
            out.append(Finding(unit, person, visit, "plays", detail))
    return out


# ---------------------------------------------------------------------------------------
# Per person: exposure history and design counts


def person_observations(study: Study, docs: Docs) -> list[tuple[str, str, int]]:
    """``(design-checks key, visit or "-", value)`` measured on one person's schedules.

    Keys are the flattened design-checks.json fields that one person's schedules
    determine (``A_assigned_learners`` and ``B_assigned_participants`` are set-level).
    """
    out: list[tuple[str, str, int]] = []
    visits = list(docs)
    everything = [it for v in visits for it in _items(docs[v])]
    families = {it["intended"]["family"] for it in everything if it.get("intended")}
    atoms = set(_ids((it for v in visits for it in _items(docs[v], "atomic_lessons")), "atom_id"))
    trained = set(
        _ids((it for v in visits for it in _items(docs[v], "message_lessons")), "message_id")
    )
    legal = _legal(atoms)
    out += [
        ("families", NA, len(families)),
        ("atomic_units_final", NA, len(atoms)),
        ("legal_messages_final", NA, len(legal)),
        ("trained_messages_final", NA, len(trained)),
        ("heldout_messages_final", NA, len(legal - trained)),
    ]
    for v in PRIMARY_VISITS[study]:
        if v in docs:
            out.append(("full_primary_trials", v, len(_items(docs[v], "trained"))))
    if study == "A":
        lessons = [(v, it) for v in visits for it in _items(docs[v], "atomic_lessons")]
        messages = [(v, it) for v in visits for it in _items(docs[v], "message_lessons")]
        out.append(("teaching_plays_A.atomic", NA, sum(int(it["plays"]) for _, it in lessons)))
        out.append(
            ("teaching_plays_A.whole_phrase", NA, sum(int(it["plays"]) for _, it in messages))
        )
        return out
    novel = set(_ids((it for v in visits for it in _items(docs[v], "novel")), "message_id"))
    out.append(("unique_B_novel_exposures_total", NA, len(novel)))
    menu_plays = sum(int(it["plays"]) for v in visits for it in _items(docs[v], "atom_menus"))
    profile_plays = sum(int(it["plays"]) for v in visits for it in _items(docs[v], "profile_menu"))
    out.append(("B_atom_menu_plays_per_person", NA, menu_plays))
    out.append(("B_extra_profile_choice_plays", NA, profile_plays))
    booked = B_SCREENING_MINUTES + sum(booked_minutes(study, v) for v in visits)
    out.append(("B_total_main_booked_minutes_per_person", NA, booked))
    # Growth by teaching visit (visits with atomic lessons), cumulative.
    cum_atoms: set[str] = set()
    cum_trained: set[str] = set()
    teaching = [v for v in visits if _items(docs[v], "atomic_lessons")]
    for i, v in enumerate(teaching):
        new_atoms = set(_ids(_items(docs[v], "atomic_lessons"), "atom_id"))
        new_trained = set(_ids(_items(docs[v], "message_lessons"), "message_id"))
        cum_atoms |= new_atoms
        cum_trained |= new_trained
        g = f"growth_counts[{i}]"
        out += [
            (f"{g}.wave", v, int(docs[v]["wave"])),
            (f"{g}.atoms_total", v, len(cum_atoms)),
            (f"{g}.legal_messages", v, len(_legal(cum_atoms))),
            (f"{g}.trained_messages", v, len(cum_trained)),
            (f"{g}.new_atoms", v, len(new_atoms)),
            (f"{g}.new_trained_messages", v, len(new_trained)),
        ]
    return out


def person_findings(study: Study, unit: str, person: str, docs: Docs) -> list[Finding]:
    """History rules over one person's visits plus the per-person design counts."""
    out: list[Finding] = []

    def bad(visit: str, rule: str, detail: str) -> None:
        out.append(Finding(unit, person, visit, rule, detail))

    expected_visits = list(study_visits(study))
    if list(docs) != expected_visits:
        bad(NA, "assessment", f"visits {list(docs)} != {expected_visits}")
    oracle = design_checks_oracle()
    for key, visit, value in person_observations(study, docs):
        if oracle[key] != value:
            bad(visit, "design-checks", f"{key} = {value}, design-checks.json gives {oracle[key]}")

    swaps = {bool(d["swap_w1_w4"]) for d in docs.values()}
    if len(swaps) != 1:
        bad(NA, "heldout-exposure", "visits disagree on the H-W1/H-W4 swap")
    swap = min(swaps) if swaps else False

    taught_atoms: set[str] = set()
    taught_messages: set[str] = set()
    novel_seen: dict[str, list[str]] = {}
    for visit, doc in docs.items():
        earlier_messages = set(taught_messages)
        taught_atoms |= set(_ids(_items(doc, "atomic_lessons"), "atom_id"))
        lessons = _ids(_items(doc, "message_lessons"), "message_id")
        taught_messages |= set(lessons)
        # Held-out exposure: any audible complete message outside the novel block.
        for b in doc["blocks"]:
            for it in b["items"]:
                mid = it.get("message_id")
                if not mid:
                    continue
                if b["block"] == "novel":
                    if mid in HELDOUT_IDS:
                        novel_seen.setdefault(mid, []).append(visit)
                    else:
                        bad(visit, "train-novel-overlap", f"trained message {mid} in novel block")
                elif mid in HELDOUT_IDS:
                    bad(visit, "heldout-exposure", f"held-out {mid} played in {b['block']}")
                if b["block"] in ("atom_menus", "profile_menu"):
                    bad(visit, "heldout-exposure", f"complete message {mid} in {b['block']}")
        listed = set(doc.get("dictionary_messages", ())) & HELDOUT_IDS
        if listed:
            bad(visit, "heldout-exposure", f"held-out in dictionary list: {sorted(listed)}")
        # Component availability.
        for mid in _ids(_items(doc, "message_lessons", "novel"), "message_id"):
            missing = [a for a in _message_atoms(mid) if a not in taught_atoms]
            if missing:
                bad(visit, "component-availability", f"{mid} before its atoms {missing}")
        for atom in _ids(_items(doc, "atomic"), "atom_id"):
            if atom not in taught_atoms:
                bad(visit, "component-availability", f"atomic probe {atom} not yet taught")
        for mid in _ids(_items(doc, "trained"), "message_id"):
            if mid not in taught_messages:
                bad(visit, "component-availability", f"trained probe {mid} not yet taught")
        for mid in _ids(_items(doc, "pre_old"), "message_id"):
            if mid not in earlier_messages:
                bad(visit, "component-availability", f"pre-old {mid} not taught earlier")
        # Training coverage of introduced indices.
        if lessons:
            introduced = _indices(taught_atoms)
            used = _indices(a for m in taught_messages for a in _message_atoms(m))
            for line, idx in introduced.items():
                if idx != used[line]:
                    detail = (
                        f"{line[0]}-{line[1]} indices {sorted(idx)} taught, "
                        f"trained messages use {sorted(used[line])}"
                    )
                    bad(visit, "training-coverage", detail)
        # Booking: the visit's fixed slots fit its booked minutes.
        slots = sum(int(it["slot_s"]) for it in _items(doc))
        if visit in expected_visits and slots > 60 * booked_minutes(study, visit):
            bad(
                visit,
                "booking",
                f"{slots} s of fixed slots exceed {booked_minutes(study, visit)} min",
            )

    # Each held-out message due for this person: exactly once, at its visit.
    for mid in sorted(HELDOUT_IDS):
        due = _heldout_visit(study, mid, swap)
        seen = novel_seen.get(mid, [])
        if due in expected_visits:
            if seen != [due]:
                bad(
                    due,
                    "heldout-exposure",
                    f"{mid} tested at {seen or 'no visit'}, due once at {due}",
                )
        elif seen:
            bad(
                seen[0],
                "heldout-exposure",
                f"{mid} is unused in Study {study} but tested at {seen}",
            )
    # Partition of the legal messages at the end.
    legal = _legal(taught_atoms)
    heldout = legal - taught_messages
    if not taught_messages <= legal:
        bad(NA, "matrix-partition", f"trained outside legal: {sorted(taught_messages - legal)}")
    if taught_messages != TRAINED_IDS or heldout != HELDOUT_IDS:
        bad(NA, "matrix-partition", "trained/held-out split differs from the matrix")
    tested = set(novel_seen)
    if not tested <= heldout:
        bad(NA, "train-novel-overlap", f"novel tests of taught messages {sorted(tested - heldout)}")
    if study == "B" and tested != heldout:
        bad(NA, "matrix-partition", f"held-out messages never tested: {sorted(heldout - tested)}")
    return out


# ---------------------------------------------------------------------------------------
# Per set: allocation (#31) and person counts


def set_findings(run: SetRun) -> list[Finding]:
    """Rules ``allocation``, ``allocation-coverage`` and set-level ``design-checks``."""
    out: list[Finding] = []

    def bad(rule: str, detail: str) -> None:
        out.append(Finding(run.prefix, NA, NA, rule, detail))

    persons = set(run.docs)
    swap_of: dict[str, bool] = {}
    if isinstance(run.allocation, AAllocation):
        problems = check_a_allocation(run.allocation)
        slots = {s.slot_id: s.unit_id for s in run.allocation.slots}
        swap_of = {b.unit_id: b.swap_w1_w4 for b in run.allocation.batches}
        allocated = len(run.allocation.slots)
        key = "A_assigned_learners"
    else:
        problems = check_b_allocation(run.allocation)
        slots = {m.slot_id: d.unit_id for d in run.allocation.dyads for m in d.members}
        swap_of = {d.unit_id: d.swap_w1_w4 for d in run.allocation.dyads}
        allocated = 2 * sum(1 for d in run.allocation.dyads if d.kind == "dyad")
        key = "B_assigned_participants"
    for p in problems:
        bad("allocation", p)
    if set(slots) != persons:
        diff = sorted(set(slots) ^ persons)
        bad("allocation-coverage", f"allocation slots and schedule persons differ: {diff[:5]}")
    for person, by_visit in run.docs.items():
        unit = run.unit_of[person]
        if slots.get(person, unit) != unit:
            bad("allocation-coverage", f"{person} is listed under {slots[person]}")
        for visit, doc in by_visit.items():
            if unit in swap_of and bool(doc["swap_w1_w4"]) != swap_of[unit]:
                out.append(
                    Finding(
                        unit, person, visit, "allocation-coverage", "swap flag differs from list"
                    )
                )
    want = (ALLOCATION_CHECKS if run.set_name == "confirmatory" else PILOT_ALLOCATION_COUNTS)[key]
    if len(run.main_persons) != want or allocated != want:
        bad(
            "design-checks",
            f"{key}: {len(run.main_persons)} scheduled, {allocated} allocated, expected {want}",
        )
    return out


def set_observations(run: SetRun) -> list[tuple[str, str, int]]:
    """Set-level design-checks observations (assigned learners or participants)."""
    key = "A_assigned_learners" if run.study == "A" else "B_assigned_participants"
    return [(key, NA, len(run.main_persons))]


# ---------------------------------------------------------------------------------------
# Reproduced oracles (report tables)


def design_check_values(runs: Sequence[SetRun]) -> dict[str, list[int]]:
    """Distinct values of every design-checks.json field observed in ``runs``.

    Per-person fields are measured on every person of every run (main and spare slots);
    ``A_assigned_learners`` / ``B_assigned_participants`` on main slots per run. A field
    is reproduced exactly when its list equals ``[oracle value]``.
    """
    seen: dict[str, set[int]] = {}
    for run in runs:
        for by_visit in run.docs.values():
            for key, _, value in person_observations(run.study, by_visit):
                seen.setdefault(key, set()).add(value)
        for key, _, value in set_observations(run):
            seen.setdefault(key, set()).add(value)
    return {k: sorted(v) for k, v in sorted(seen.items())}


def assessment_values(runs: Sequence[SetRun]) -> dict[tuple[str, str], dict[str, list[int]]]:
    """Distinct generated counts and seconds per (study, visit) over all persons."""
    out: dict[tuple[str, str], dict[str, set[int]]] = {}
    for run in runs:
        for by_visit in run.docs.values():
            for visit, doc in by_visit.items():
                row = out.setdefault((run.study, visit), {})
                for k, v in visit_assessment(doc).items():
                    row.setdefault(k, set()).add(v)
    return {key: {k: sorted(v) for k, v in row.items()} for key, row in sorted(out.items())}


def _fmt(values: Sequence[int] | None) -> str:
    return "missing" if not values else ", ".join(str(v) for v in values)


def report(runs: Sequence[SetRun], findings: Sequence[Finding]) -> str:
    """Markdown report: sets checked, reproduced design checks and assessment, findings."""
    lines = ["## Schedule checks (#32)", ""]
    lines += ["| study | set | seed | units | persons | schedules | run sheets |"]
    lines += ["| --- | --- | --- | ---: | ---: | ---: | ---: |"]
    for run in runs:
        n = sum(len(v) for v in run.docs.values())
        sheets = sum(len(v) for v in run.run_sheets.values())
        lines.append(
            f"| {run.study} | {run.set_name} | `{run.master.label}` | {len(run.units)} | "
            f"{len(run.docs)} | {n} | {sheets} |"
        )
    oracle = design_checks_oracle()
    by_set: dict[str, list[SetRun]] = {}
    for run in runs:
        by_set.setdefault(run.set_name, []).append(run)
    for set_name, group in by_set.items():
        values = design_check_values(group)
        want = dict(oracle)
        if set_name == "pilot":
            want.update(PILOT_ALLOCATION_COUNTS)
        studies = {r.study for r in group}
        lines += ["", f"### design-checks.json reproduced ({set_name})", ""]
        lines += ["| field | expected | generated | ok |", "| --- | ---: | ---: | --- |"]
        for key in sorted(want):
            if key.startswith(("A_", "teaching_plays_A")) and "A" not in studies:
                continue
            if (key.startswith(("B_", "growth", "unique_B"))) and "B" not in studies:
                continue
            got = values.get(key)
            ok = "yes" if got == [want[key]] else "NO"
            lines.append(f"| `{key}` | {want[key]} | {_fmt(got)} | {ok} |")
    lines += ["", "### assessment-schedule.csv reproduced", ""]
    lines += [
        "| study | visit | pre-old | trained | novel | atomic | extra | seconds | ok |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for (study, visit), row in assessment_values(runs).items():
        want_row = _oracle_row(study, visit) or {}
        cols = [
            "pre_old_trained",
            "post_trained",
            "novel_once",
            "atomic",
            "extra_after_protected",
            "assessment_seconds",
        ]
        same = all(row.get(c) == [want_row.get(c)] for c in cols)
        cells_ = " | ".join(_fmt(row.get(c)) for c in cols)
        lines.append(f"| {study} | {visit} | {cells_} | {'yes' if same else 'NO'} |")
    lines += ["", "### Findings", "", format_findings(findings)]
    return "\n".join(lines)
