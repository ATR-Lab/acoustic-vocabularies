"""Unmasking: condition labels joined at analysis time only (#34).

The only module that reads ``keys/`` (``paths.INPUT_PATHS["book_key"]``: Study A book ID ->
method A1/A2/A3 and designer D1-D3, ``av-schedules/a-book-key``). Study B roles, the
scaffold allocation (``structured_family``) and the W1/W4 swap come from the dyad list
(``inputs/schedules/B/<set>-dyads.json``, ``av-schedules/b-dyads``); profile family per
Study A batch from the slot list. Lists are verified with their ``list_sha256`` (as
``av_schedules.assign_output.load_list``). Nothing from this module is ever written to
``reconciled/`` or ``monitoring/``.

Assigned persons (``Conditions.planned``, the all-assigned denominator): when the reveal
log ``inputs/reveal/<study>-<set>.jsonl`` exists, the slots it revealed (Study B: both
members of every revealed dyad slot, spares included; a main slot replaced by a spare
is not assigned); otherwise every main-list slot (Study A: all learner slots of the
batches; Study B: the main dyad slots). The log's line chain and its ``list_sha256``
are checked. A REAL root refuses lists marked ``demo`` and a SYNTHETIC root refuses lists
that are not.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from av_schedules.assign_output import load_list

from .paths import INPUT_PATHS, DataRoot

GENESIS: Final = "0" * 64


class UnmaskError(ValueError):
    """Missing, edited or mismatched unmasking inputs."""


@dataclass(frozen=True)
class BookCondition:
    """Study A: the method of a book (restricted)."""

    book_id: str
    unit_id: str
    method: str  # "A1", "A2", "A3"
    designer: str | None  # "D1".."D3" for A1
    profile: str  # "P1".."P3": profile family of the batch (bootstrap stratum)


@dataclass(frozen=True)
class DyadCondition:
    """Study B: roles and scaffold allocation of a dyad slot (restricted)."""

    unit_id: str
    active_person: str
    yoked_person: str
    structured_family: str  # "K" or "Q"
    swap_w1_w4: bool  # heldout-set order (bootstrap stratum)


@dataclass(frozen=True)
class Conditions:
    """Restricted condition labels of one study and set, as the estimators need them."""

    study: str
    set_name: str
    books: Mapping[str, BookCondition]  # A: book ID -> condition (empty for B)
    dyads: Mapping[str, DyadCondition]  # B: dyad slot -> condition (empty for A)
    person_unit: Mapping[str, str]  # person slot -> batch (A) or dyad slot (B)
    person_condition: Mapping[str, str]  # person slot -> A1/A2/A3 (A), active/yoked (B)
    planned: Mapping[str, tuple[str, ...]]  # unit -> every assigned person slot (all-assigned)
    person_book: Mapping[str, str] = field(default_factory=dict)  # A: person slot -> book ID
    list_sha256: Mapping[str, str] = field(default_factory=dict)  # input file -> list hash
    planned_source: str = "list"  # "list" (main list) or "reveal-log"


def _input(root: DataRoot, key: str, **fields: str) -> tuple[str, Any]:
    rel = INPUT_PATHS[key].format(**fields)
    area, _, sub = rel.partition("/")
    path = root.input_path(area, sub)  # type: ignore[arg-type]
    if not path.is_file():
        raise UnmaskError(f"missing unmasking input {rel}")
    try:
        doc = load_list(path)
    except (ValueError, OSError) as exc:
        raise UnmaskError(f"{rel}: {exc}") from None
    if doc.get("set") != fields.get("set"):
        raise UnmaskError(f"{rel}: set is {doc.get('set')!r}")
    demo = doc.get("demo") is True
    if root.synthetic != demo:
        raise UnmaskError(
            f"{rel}: a {root.data_kind} root refuses lists with demo={str(demo).lower()}"
        )
    return rel, doc


def load_a_conditions(root: DataRoot, set_name: str) -> Mapping[str, BookCondition]:
    """Book ID -> condition."""
    return _load_a(root, set_name)[0]


def _load_a(
    root: DataRoot, set_name: str
) -> tuple[dict[str, BookCondition], dict[str, Any], dict[str, Any], dict[str, str]]:
    key_rel, key = _input(root, "book_key", set=set_name)
    slots_rel, slots = _input(root, "slots", set=set_name)
    if key.get("format") != "av-schedules/a-book-key":
        raise UnmaskError(f"{key_rel} is not a Study A book key")
    if slots.get("format") != "av-schedules/a-slots":
        raise UnmaskError(f"{slots_rel} is not a Study A slot list")
    if key["slots_list_sha256"] != slots["list_sha256"]:
        raise UnmaskError(f"{key_rel} belongs to another slot list")
    profile: dict[str, str] = {}
    slot_book: dict[str, str] = {}
    for s in slots["slots"]:
        profile[s["unit_id"]] = s["profile"]
        slot_book[s["slot_id"]] = s["book_id"]
    books: dict[str, BookCondition] = {}
    for b in key["books"]:
        for slot in b["slots"]:
            if slot_book.get(slot) != b["book_id"]:
                raise UnmaskError(f"{key_rel}: slot {slot} is not listed with {b['book_id']}")
        books[b["book_id"]] = BookCondition(
            b["book_id"], b["unit_id"], b["method"], b["designer"], profile[b["unit_id"]]
        )
    hashes = {key_rel: key["list_sha256"], slots_rel: slots["list_sha256"]}
    return dict(sorted(books.items())), key, slots, hashes


def load_b_conditions(root: DataRoot, set_name: str) -> Mapping[str, DyadCondition]:
    """Dyad slot -> condition."""
    return _load_b(root, set_name)[0]


def _load_b(
    root: DataRoot, set_name: str
) -> tuple[dict[str, DyadCondition], dict[str, Any], dict[str, str]]:
    rel, doc = _input(root, "dyads", set=set_name)
    if doc.get("format") != "av-schedules/b-dyads":
        raise UnmaskError(f"{rel} is not a Study B dyad list")
    dyads: dict[str, DyadCondition] = {}
    for d in doc["dyads"]:
        roles = {m["role"]: m["slot_id"] for m in d["members"]}
        if set(roles) != {"active", "yoked"}:
            raise UnmaskError(f"{rel}: dyad {d['unit_id']} lacks an active and a yoked member")
        dyads[d["unit_id"]] = DyadCondition(
            d["unit_id"], roles["active"], roles["yoked"], d["structured_family"], d["swap_w1_w4"]
        )
    return dict(sorted(dyads.items())), doc, {rel: doc["list_sha256"]}


def revealed_slots(root: DataRoot, study: str, set_name: str, list_sha256: str) -> set[str] | None:
    """Person slots revealed in the reveal log (None when there is no log yet)."""
    rel = INPUT_PATHS["reveal_log"].format(study=study, set=set_name)
    path = root.input_path("inputs", rel.removeprefix("inputs/"))
    if not path.is_file():
        return None
    prev = GENESIS
    out: set[str] = set()
    for n, raw in enumerate(path.read_bytes().splitlines(keepends=True), start=1):
        try:
            line = json.loads(raw)
        except ValueError:
            raise UnmaskError(f"{rel} line {n}: not JSON") from None
        if line.get("line") != n or line.get("prev_sha256") != prev:
            raise UnmaskError(f"{rel} line {n}: broken line chain")
        if line.get("list_sha256") != list_sha256:
            raise UnmaskError(f"{rel} line {n}: written against another list")
        prev = hashlib.sha256(raw).hexdigest()
        if line.get("event") != "reveal":
            continue
        entry = line["entry"]
        if "members" in entry:
            out.update(m["slot_id"] for m in entry["members"])
        else:
            out.add(entry["slot_id"])
    return out


def load_conditions(root: DataRoot, study: str, set_name: str) -> Conditions:
    """Conditions of one study and set (slot or dyad list plus, for A, the book key)."""
    if study == "A":
        books, key, slots, hashes = _load_a(root, set_name)
        person_unit: dict[str, str] = {}
        person_condition: dict[str, str] = {}
        person_book: dict[str, str] = {}
        for b in key["books"]:
            for slot in b["slots"]:
                person_unit[slot] = b["unit_id"]
                person_condition[slot] = b["method"]
                person_book[slot] = b["book_id"]
        revealed = revealed_slots(root, "A", set_name, slots["list_sha256"])
        planned: dict[str, list[str]] = {}
        for slot in sorted(person_unit):
            if revealed is None or slot in revealed:
                planned.setdefault(person_unit[slot], []).append(slot)
        return Conditions(
            study="A",
            set_name=set_name,
            books=books,
            dyads={},
            person_unit=dict(sorted(person_unit.items())),
            person_condition=dict(sorted(person_condition.items())),
            planned={u: tuple(p) for u, p in sorted(planned.items())},
            person_book=dict(sorted(person_book.items())),
            list_sha256=hashes,
            planned_source="list" if revealed is None else "reveal-log",
        )
    if study != "B":
        raise ValueError(f"unknown study {study!r}")
    dyads, doc, hashes = _load_b(root, set_name)
    kinds = {d["unit_id"]: d["kind"] for d in doc["dyads"]}
    revealed = revealed_slots(root, "B", set_name, doc["list_sha256"])
    person_unit = {}
    person_condition = {}
    planned_b: dict[str, tuple[str, ...]] = {}
    for unit, d in dyads.items():
        person_unit[d.active_person] = unit
        person_unit[d.yoked_person] = unit
        person_condition[d.active_person] = "active"
        person_condition[d.yoked_person] = "yoked"
        members = tuple(sorted((d.active_person, d.yoked_person)))
        if revealed is None:
            if kinds[unit] == "dyad":
                planned_b[unit] = members
        elif any(m in revealed for m in members):
            planned_b[unit] = members
    return Conditions(
        study="B",
        set_name=set_name,
        books={},
        dyads=dyads,
        person_unit=dict(sorted(person_unit.items())),
        person_condition=dict(sorted(person_condition.items())),
        planned=planned_b,
        list_sha256=hashes,
        planned_source="list" if revealed is None else "reveal-log",
    )
