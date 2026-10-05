"""Lesson and trial orders: per-person visit schedules with stored seeds (#30).

One schedule document per person and visit (Study A: ``D0``, ``D7``; Study B: ``V1``,
``V2``, ``V3``, ``W1``, ``W4``). A schedule lists ordered blocks of items (lessons, menus,
pre-teaching probes, the protected battery trained -> novel -> atomic and the final
task-validity block) with fixed slot lengths and the private intended tuple of every
item, so schedule files are hidden-answer material.

* Block structure and counts come from :func:`visit_plan`, derived from the matrix.
* Seeded blocks use ``derive_seed(master, study, unit_id, person, visit, block)`` =
  ``sha256("{master}|{study}|{unit_id}|{person}|{visit}|{block}")``. Blocks shared by
  every person of a unit use the unit ID as the person token. Each block stores its seed
  and its tokens. The public per-unit ``seed`` of #29 is never used.
* Stored orders come from the unit (#29): Study A atomic lessons follow ``atom_order``
  (shared within the batch); Study B atom menus and atomic lessons follow the wave
  order (shared within the dyad).
* Two-pass blocks are independent shuffles per pass, so every item appears once per
  pass. Lesson passes alternate the families item by item; pass 1 starts with the unit's
  ``family_first`` and pass 2 with the other family.
* The validity block holds 8 no-cue targets drawn uniformly with replacement from the
  32 legal commands and the 8 commands of the frozen speech list, in one seeded order.

See ``schedules/docs/orders.md`` and ``docs/interfaces/schedules.md``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from typing import Any, Final, Literal, TypeVar

from . import __version__
from .curriculum import dumps_json, novel_by_visit, permutation_json
from .design import SET_CODE, BDyadSlot, SetName, Unit
from .findings import Finding
from .matrix import (
    A_VISITS,
    B_VISIT_WAVE,
    B_VISITS,
    FAMILIES,
    HELDOUT_SETS,
    LABELS,
    Cell,
    Family,
    Study,
    atom_wave,
    atoms,
    cells,
    novel_visit,
    parse_atom_id,
    trained_cells,
)
from .seeds import MasterSeed, SeedStream, derive_seed

T = TypeVar("T")

VISIT_SCHEDULE_FORMAT: Final = "av-schedules/visit-schedule"
VISIT_SCHEDULE_FORMAT_VERSION: Final = 1
SPEECH_LIST_FORMAT: Final = "av-schedules/speech-list"
SPEECH_LIST_FORMAT_VERSION: Final = 1

TrialType = Literal[
    "profile_menu",
    "atom_menu",
    "atomic_lesson",
    "message_lesson",
    "pre_old",
    "trained",
    "novel",
    "atomic",
    "no_cue",
    "speech",
]
TRIAL_TYPES: Final[tuple[TrialType, ...]] = (
    "profile_menu",
    "atom_menu",
    "atomic_lesson",
    "message_lesson",
    "pre_old",
    "trained",
    "novel",
    "atomic",
    "no_cue",
    "speech",
)
# Fixed slot length in seconds (Protocol constants; Study B protocol section 5).
SLOT_S: Final[dict[str, int]] = {
    "profile_menu": 60,
    "atom_menu": 45,
    "atomic_lesson": 20,
    "message_lesson": 24,
    "pre_old": 14,
    "trained": 14,
    "novel": 14,
    "atomic": 9,
    "no_cue": 14,
    "speech": 14,
}
# Scheduled audio plays per item (lessons 3; menus 8; one cue per test trial; none
# for a no-cue trial).
PLAYS: Final[dict[str, int]] = {
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
# ``trained_status`` per trial type: matrix status of the cue (trained or held-out
# message, atom), ``nonsemantic`` for the profile menu, ``validity`` for the no-cue and
# speech controls (no acoustic-vocabulary cue).
TRAINED_STATUS: Final[dict[str, str]] = {
    "profile_menu": "nonsemantic",
    "atom_menu": "atom",
    "atomic_lesson": "atom",
    "message_lesson": "trained",
    "pre_old": "trained",
    "trained": "trained",
    "novel": "heldout",
    "atomic": "atom",
    "no_cue": "validity",
    "speech": "validity",
}

BlockName = Literal[
    "profile_menu",
    "atom_menus",
    "atomic_lessons",
    "message_lessons",
    "pre_old",
    "trained",
    "novel",
    "atomic",
    "validity",
]
Phase = Literal["selection", "teaching", "pre_test", "protected", "validity"]
BLOCK_PHASE: Final[dict[str, Phase]] = {
    "profile_menu": "selection",
    "atom_menus": "selection",
    "atomic_lessons": "teaching",
    "message_lessons": "teaching",
    "pre_old": "pre_test",
    "trained": "protected",
    "novel": "protected",
    "atomic": "protected",
    "validity": "validity",
}
BLOCK_CODE: Final[dict[str, str]] = {
    "profile_menu": "PM",
    "atom_menus": "AM",
    "atomic_lessons": "AL",
    "message_lessons": "ML",
    "pre_old": "PO",
    "trained": "TR",
    "novel": "NV",
    "atomic": "AT",
    "validity": "VA",
}
# Trial type of every item of a block (the validity block mixes no_cue and speech).
BLOCK_TRIAL_TYPE: Final[dict[str, TrialType]] = {
    "profile_menu": "profile_menu",
    "atom_menus": "atom_menu",
    "atomic_lessons": "atomic_lesson",
    "message_lessons": "message_lesson",
    "pre_old": "pre_old",
    "trained": "trained",
    "novel": "novel",
    "atomic": "atomic",
}
PROTECTED_BATTERY: Final[tuple[str, ...]] = ("trained", "novel", "atomic")
ASSESSMENT_BLOCKS: Final[tuple[str, ...]] = ("pre_old", "trained", "novel", "atomic")
# Blocks that may only hold trained complete messages (never a held-out message).
NO_HELDOUT_BLOCKS: Final[tuple[str, ...]] = (
    "profile_menu",
    "atom_menus",
    "atomic_lessons",
    "message_lessons",
    "pre_old",
)

# Person slots (shared with the allocation lists, #31). Every Study A learner slot and
# every Study B member slot gets its own schedule; schedules never depend on the method
# or on the dyad role.
A_LEARNER_SLOTS: Final[dict[SetName, int]] = {"pilot": 6, "confirmatory": 12}
B_MEMBER_SLOTS: Final[tuple[str, ...]] = ("M1", "M2")

VALIDITY_NO_CUE: Final = 8
VALIDITY_SPEECH: Final = 8
SPEECH_PURPOSE: Final = "speech-list"


# ---------------------------------------------------------------------------------------
# Visit plans (expected block structure; the counts oracle for run sheets, #32)


@dataclass(frozen=True)
class BlockPlan:
    """One block of a visit: name, phase, item count, passes and slot length."""

    block: BlockName
    count: int
    passes: int
    slot_s: int

    @property
    def phase(self) -> Phase:
        return BLOCK_PHASE[self.block]

    @property
    def seconds(self) -> int:
        return self.count * self.slot_s


def study_visits(study: Study) -> tuple[str, ...]:
    """Visits with a schedule: Study A ``D0``, ``D7``; Study B ``V1``..``W4``."""
    return A_VISITS if study == "A" else B_VISITS


def visit_wave(study: Study, visit: str) -> int:
    """Inventory wave available at a visit (Study A teaches everything at D0)."""
    if visit not in study_visits(study):
        raise ValueError(f"unknown {study} visit {visit!r}")
    return 3 if study == "A" else B_VISIT_WAVE[visit]


def final_visit(study: Study) -> str:
    """The visit that ends with the task-validity block (A ``D7``, B ``W4``)."""
    return study_visits(study)[-1]


def teaching_visit(study: Study, visit: str) -> bool:
    """True if new atoms and messages are taught at this visit (A ``D0``, B ``V1``..``V3``)."""
    if study == "A":
        return visit == A_VISITS[0]
    return visit in ("V1", "V2", "V3")


@cache
def trained_upto(wave: int) -> tuple[Cell, ...]:
    """Trained cells introduced at waves 1..``wave`` (planning order)."""
    return tuple(
        c for c in trained_cells() if c.training_wave is not None and c.training_wave <= wave
    )


@cache
def atoms_upto(wave: int) -> tuple[str, ...]:
    """Atoms introduced at waves 1..``wave`` (canonical order)."""
    return tuple(a for a in atoms() if atom_wave(a) <= wave)


@cache
def new_trained(study: Study, visit: str) -> tuple[Cell, ...]:
    """Messages newly taught at a visit (A D0: all 18; B: that wave's trained cells)."""
    if not teaching_visit(study, visit):
        return ()
    return trained_cells() if study == "A" else trained_cells(B_VISIT_WAVE[visit])


@cache
def new_atoms(study: Study, visit: str) -> tuple[str, ...]:
    """Atoms newly taught at a visit (canonical order)."""
    if not teaching_visit(study, visit):
        return ()
    if study == "A":
        return atoms()
    w = B_VISIT_WAVE[visit]
    return tuple(a for a in atoms() if atom_wave(a) == w)


@cache
def novel_count(study: Study, visit: str) -> int:
    """Held-out messages tested at a visit (the same with or without the W1/W4 swap)."""
    return sum(
        1
        for hs in HELDOUT_SETS
        for c in cells()
        if c.heldout_set == hs and novel_visit(study, hs, False) == visit
    )


@cache
def visit_plan(study: Study, visit: str) -> tuple[BlockPlan, ...]:
    """Ordered blocks of a visit with expected counts, passes and slot lengths.

    Study A D0: atomic lessons, message lessons (2 passes), trained (2 passes), novel,
    atomic. D7: trained (2 passes), novel, atomic, validity. Study B V1..V3: pre-old
    (V2, V3: all previously trained messages once), profile menu (V1), atom menus and
    atomic lessons for the new atoms, message lessons (2 passes of the new messages),
    then trained (every cumulative trained message once), novel, atomic. W1, W4: trained
    (2 passes of 18), novel, atomic; W4 adds the validity block.
    """
    wave = visit_wave(study, visit)
    out: list[BlockPlan] = []

    def add(block: BlockName, count: int, passes: int = 1) -> None:
        trial_type = BLOCK_TRIAL_TYPE.get(block, "no_cue")
        out.append(BlockPlan(block, count, passes, SLOT_S[trial_type]))

    teaching = teaching_visit(study, visit)
    if study == "B" and teaching and wave > 1:
        add("pre_old", len(trained_upto(wave - 1)))
    if study == "B" and wave == 1 and teaching:
        add("profile_menu", 1)
    if teaching:
        if study == "B":
            add("atom_menus", len(new_atoms(study, visit)))
        add("atomic_lessons", len(new_atoms(study, visit)))
        add("message_lessons", 2 * len(new_trained(study, visit)), passes=2)
    # Study B acquisition visits test every cumulative trained message once; primary
    # visits (A D0/D7, B W1/W4) test all 18 in two passes.
    trained_passes = 1 if (study == "B" and teaching) else 2
    add("trained", trained_passes * len(trained_upto(wave)), passes=trained_passes)
    add("novel", novel_count(study, visit))
    add("atomic", len(atoms_upto(wave)))
    if visit == final_visit(study):
        add("validity", VALIDITY_NO_CUE + VALIDITY_SPEECH)
    return tuple(out)


def assessment_counts(study: Study, visit: str) -> dict[str, int]:
    """The visit's row of planning ``assessment-schedule.csv`` derived from the plan.

    Keys: ``pre_old_trained``, ``post_trained``, ``novel_once``, ``atomic``,
    ``assessment_seconds`` (pre-old and protected slots), ``extra_after_protected``.
    """
    return dict(_assessment_counts(study, visit))


@cache
def _assessment_counts(study: Study, visit: str) -> tuple[tuple[str, int], ...]:
    plan = {p.block: p for p in visit_plan(study, visit)}

    def count(block: str) -> int:
        return plan[block].count if block in plan else 0

    return (
        ("pre_old_trained", count("pre_old")),
        ("post_trained", count("trained")),
        ("novel_once", count("novel")),
        ("atomic", count("atomic")),
        ("assessment_seconds", sum(plan[b].seconds for b in ASSESSMENT_BLOCKS if b in plan)),
        ("extra_after_protected", count("validity")),
    )


# ---------------------------------------------------------------------------------------
# Persons and seeds


def person_ids(unit: Unit) -> tuple[str, ...]:
    """Person slots of a unit: A ``<unit>-L01``.. (12 confirmatory, 6 pilot); B ``-M1``, ``-M2``."""
    if unit.study == "A":
        n = A_LEARNER_SLOTS[unit.set_name]
        return tuple(f"{unit.unit_id}-L{i:02d}" for i in range(1, n + 1))
    return tuple(f"{unit.unit_id}-{m}" for m in B_MEMBER_SLOTS)


def seed_tokens(study: Study, unit_id: str, person: str, visit: str, block: str) -> tuple[str, ...]:
    """Seed tokens of one block: ``study|unit|person|visit|block``.

    ``person`` is the person slot ID, or the unit ID for blocks shared by the unit.
    """
    return (study, unit_id, person, visit, block)


def pass_orders(stream: SeedStream, items: Sequence[T], passes: int) -> tuple[tuple[T, ...], ...]:
    """``passes`` independent Fisher-Yates shuffles of ``items``: each item once per pass."""
    return tuple(stream.permutation(items) for _ in range(passes))


def alternating_passes(
    stream: SeedStream,
    by_family: Mapping[Family, Sequence[T]],
    family_first: Family,
    passes: int,
) -> tuple[tuple[T, ...], ...]:
    """Lesson passes with alternating families.

    Pass ``p`` (1-based) starts with ``family_first`` when ``p`` is odd and with the other
    family when ``p`` is even; the two families then alternate item by item. Within each
    family the order is a fresh shuffle per pass (the leading family is drawn first).
    Both families must hold the same number of items.
    """
    other: Family = "Q" if family_first == "K" else "K"
    if len(by_family[family_first]) != len(by_family[other]):
        raise ValueError("alternating passes need equal item counts per family")
    out = []
    for p in range(passes):
        lead, second = (family_first, other) if p % 2 == 0 else (other, family_first)
        a = stream.permutation(by_family[lead])
        b = stream.permutation(by_family[second])
        out.append(tuple(x for pair in zip(a, b, strict=True) for x in pair))
    return tuple(out)


def sample_with_replacement(stream: SeedStream, items: Sequence[T], k: int) -> tuple[T, ...]:
    """``k`` independent uniform draws from ``items`` (repeats allowed)."""
    return tuple(items[stream.randbelow(len(items))] for _ in range(k))


# ---------------------------------------------------------------------------------------
# Frozen speech list (one per study and set; recorded by #71)


@dataclass(frozen=True)
class SpeechCommand:
    """One spoken validity command in semantic-label space."""

    family: Family
    semantic_action: str
    semantic_referent: str

    @property
    def speech_id(self) -> str:
        """``<family>-<ACTION>-<TARGET>``, e.g. ``K-ADD_ONE-B`` (unique among 32 commands)."""
        return f"{self.family}-{self.semantic_action}-{self.semantic_referent}"


def speech_seed_tokens(study: Study, set_name: SetName) -> tuple[str, ...]:
    """Seed tokens of a speech list: ``study|<study>-<P|C>|speech-list``."""
    return (study, f"{study}-{SET_CODE[set_name]}", SPEECH_PURPOSE)


def speech_commands(
    master: MasterSeed, study: Study, set_name: SetName
) -> tuple[SpeechCommand, ...]:
    """The frozen balanced speech list: each action and each target exactly once.

    Per family (K then Q) the four actions, in label order, are paired with a seeded
    permutation of the family's four targets (one K pairing with trays A-D, one Q
    pairing with containers E-H).
    """
    s = SeedStream(derive_seed(master, *speech_seed_tokens(study, set_name)))
    out: list[SpeechCommand] = []
    for f in FAMILIES:
        targets = s.permutation(LABELS[f]["referent"])
        for action, target in zip(LABELS[f]["action"], targets, strict=True):
            out.append(SpeechCommand(f, action, target))
    return tuple(out)


def speech_list_document(master: MasterSeed, study: Study, set_name: SetName) -> dict[str, Any]:
    """The ``<set>-speech-list.json`` document (schema ``speech-list.schema.json``)."""
    tokens = speech_seed_tokens(study, set_name)
    return {
        "format": SPEECH_LIST_FORMAT,
        "format_version": SPEECH_LIST_FORMAT_VERSION,
        "generator": {"name": "av-schedules", "version": __version__},
        "demo": master.demo,
        "seed_label": master.label,
        "study": study,
        "set": set_name,
        "seed": derive_seed(master, *tokens),
        "seed_tokens": list(tokens),
        "commands": [
            {
                "position": i,
                "speech_id": c.speech_id,
                "family": c.family,
                "semantic_action": c.semantic_action,
                "semantic_referent": c.semantic_referent,
            }
            for i, c in enumerate(speech_commands(master, study, set_name), start=1)
        ],
    }


def speech_list_json(master: MasterSeed, study: Study, set_name: SetName) -> bytes:
    return dumps_json(speech_list_document(master, study, set_name))


@cache
def _speech(master: MasterSeed, study: Study, set_name: SetName) -> tuple[str, str]:
    """(seed, SHA-256 of the speech-list file) for validity blocks."""
    doc = speech_list_document(master, study, set_name)
    return doc["seed"], hashlib.sha256(dumps_json(doc)).hexdigest()


# ---------------------------------------------------------------------------------------
# Schedule documents


def _message_intended(unit: Unit, c: Cell) -> dict[str, Any]:
    return {
        "kind": "message",
        "family": c.family,
        "message_id": c.message_id,
        "action_index": c.action_index,
        "referent_index": c.referent_index,
        "semantic_action": unit.permutation.label(c.family, "action", c.action_index),
        "semantic_referent": unit.permutation.label(c.family, "referent", c.referent_index),
    }


_ATOM_PARTS: Final = {a: parse_atom_id(a) for a in atoms()}


def _atom_intended(unit: Unit, atom: str) -> dict[str, Any]:
    family, role, index = _ATOM_PARTS[atom]
    return {
        "kind": "atom",
        "family": family,
        "atom_id": atom,
        "role": role,
        "index": index,
        "semantic_label": unit.permutation.label(family, role, index),
    }


def _presentation(unit: Unit, family: str) -> str:
    """Whole-message lesson presentation: A always structured; B by SQ arm."""
    if isinstance(unit, BDyadSlot):
        return "structured" if family == unit.structured_family else "dictionary"
    return "structured"


def _cell_of(unit: Unit, cmd: SpeechCommand) -> Cell:
    a = unit.permutation.index_of(cmd.family, "action", cmd.semantic_action)
    r = unit.permutation.index_of(cmd.family, "referent", cmd.semantic_referent)
    return Cell(cmd.family, a, r)


@dataclass(frozen=True)
class OrderedItem:
    """One scheduled item before numbering: trial type, pass and cue.

    ``cell`` is the played message, or the private target of a no-cue or speech trial;
    ``atom`` the played atom; ``speech`` the spoken command.
    """

    trial_type: TrialType
    pass_no: int
    cell: Cell | None = None
    atom: str | None = None
    speech: SpeechCommand | None = None

    @property
    def item_id(self) -> str | None:
        """Message ID (messages, no-cue targets), atom ID or speech ID."""
        if self.speech is not None:
            return self.speech.speech_id
        if self.cell is not None:
            return self.cell.message_id
        return self.atom


@dataclass(frozen=True)
class BlockOrder:
    """The ordered items of one block with the seed they were drawn from."""

    plan: BlockPlan
    shared_by: str  # "person", "batch" (A) or "dyad" (B)
    order_source: str  # "seeded", "stored" or "fixed"
    seed: str | None
    seed_tokens: tuple[str, ...] | None
    items: tuple[OrderedItem, ...]
    no_cue_targets: tuple[Cell, ...] = ()


def _item(unit: Unit, prefix: str, position: int, e: OrderedItem) -> dict[str, Any]:
    tt = e.trial_type
    intended: dict[str, Any] | None = None
    if e.cell is not None:
        intended = _message_intended(unit, e.cell)
    elif e.atom is not None:
        intended = _atom_intended(unit, e.atom)
    audible_message = e.cell is not None and tt not in ("no_cue", "speech")
    return {
        "trial_id": f"{prefix}-{position:02d}",
        "position": position,
        "trial_type": tt,
        "pass": e.pass_no,
        "slot_s": SLOT_S[tt],
        "plays": PLAYS[tt],
        "message_id": e.cell.message_id if audible_message and e.cell is not None else None,
        "atom_id": e.atom,
        "speech_id": e.speech.speech_id if e.speech is not None else None,
        "trained_status": TRAINED_STATUS[tt],
        "presentation": (
            _presentation(unit, e.cell.family)
            if tt == "message_lesson" and e.cell is not None
            else None
        ),
        "intended": intended,
    }


def _cells_by_family(cs: Sequence[Cell]) -> dict[Family, tuple[Cell, ...]]:
    return {f: tuple(c for c in cs if c.family == f) for f in FAMILIES}


def _novel_cells(unit: Unit, visit: str) -> tuple[Cell, ...]:
    ids = novel_by_visit(unit)[visit]
    return tuple(c for c in cells() if c.message_id in ids)


def _stored_atom_order(unit: Unit, visit: str) -> tuple[str, ...]:
    if unit.study == "A":
        return unit.atom_order
    return unit.wave_orders[B_VISIT_WAVE[visit] - 1]


def _check_inputs(master: MasterSeed, unit: Unit, person_id: str, visit: str) -> None:
    if unit.seed_label != master.label:
        raise ValueError(f"unit {unit.unit_id} was built from another master seed")
    if person_id not in person_ids(unit):
        raise ValueError(f"{person_id!r} is not a person slot of unit {unit.unit_id}")
    visit_wave(unit.study, visit)  # raises for an unknown visit


def block_order(
    master: MasterSeed, unit: Unit, person_id: str, visit: str, block: str
) -> BlockOrder:
    """Ordered items of one block of a person's visit (the core of the generator).

    Seeded blocks draw from ``derive_seed(master, study, unit_id, person, visit, block)``
    (person = unit ID for shared blocks):

    * ``message_lessons``: :func:`alternating_passes` over the newly trained messages
      (planning order per family), leading with ``unit.family_first`` in pass 1;
    * ``pre_old``, ``trained``: :func:`pass_orders` over the cumulative trained messages
      (planning order); ``novel``: one shuffle of the visit's held-out messages;
      ``atomic``: one shuffle of the cumulative atoms (canonical order);
    * ``validity``: 8 draws of :func:`sample_with_replacement` over the 32 cells (planning
      order), then one shuffle of the 8 no-cue trials followed by the 8 speech commands.

    Stored blocks (atom menus, atomic lessons) follow the unit's atom order (A) or wave
    order (B); the profile menu is a single fixed item.
    """
    _check_inputs(master, unit, person_id, visit)
    study = unit.study
    wave = visit_wave(study, visit)
    plans = {p.block: p for p in visit_plan(study, visit)}
    if block not in plans:
        raise ValueError(f"{study} {visit} has no {block!r} block")
    plan = plans[block]
    shared = block in ("profile_menu", "atom_menus", "atomic_lessons") or (
        block == "message_lessons" and study == "B"
    )
    shared_by = ("batch" if study == "A" else "dyad") if shared else "person"
    if block == "profile_menu":
        return BlockOrder(plan, shared_by, "fixed", None, None, (OrderedItem("profile_menu", 1),))
    if block in ("atom_menus", "atomic_lessons"):
        tt = BLOCK_TRIAL_TYPE[block]
        entries = tuple(OrderedItem(tt, 1, atom=a) for a in _stored_atom_order(unit, visit))
        return BlockOrder(plan, shared_by, "stored", None, None, entries)

    tokens = seed_tokens(study, unit.unit_id, unit.unit_id if shared else person_id, visit, block)
    seed = derive_seed(master, *tokens)
    stream = SeedStream(seed)
    targets: tuple[Cell, ...] = ()
    if block == "message_lessons":
        by_family = _cells_by_family(new_trained(study, visit))
        passes = alternating_passes(stream, by_family, unit.family_first, plan.passes)
        entries = tuple(
            OrderedItem("message_lesson", p, cell=c)
            for p, order in enumerate(passes, start=1)
            for c in order
        )
    elif block in ("pre_old", "trained"):
        pool = trained_upto(wave - 1 if block == "pre_old" else wave)
        tt = BLOCK_TRIAL_TYPE[block]
        entries = tuple(
            OrderedItem(tt, p, cell=c)
            for p, order in enumerate(pass_orders(stream, pool, plan.passes), start=1)
            for c in order
        )
    elif block == "novel":
        entries = tuple(
            OrderedItem("novel", 1, cell=c) for c in stream.permutation(_novel_cells(unit, visit))
        )
    elif block == "atomic":
        entries = tuple(
            OrderedItem("atomic", 1, atom=a) for a in stream.permutation(atoms_upto(wave))
        )
    else:  # validity
        targets = sample_with_replacement(stream, cells(), VALIDITY_NO_CUE)
        spoken = speech_commands(master, study, unit.set_name)
        combined = [OrderedItem("no_cue", 1, cell=c) for c in targets] + [
            OrderedItem("speech", 1, cell=_cell_of(unit, cmd), speech=cmd) for cmd in spoken
        ]
        entries = stream.permutation(combined)
    return BlockOrder(plan, shared_by, "seeded", seed, tokens, entries, targets)


def build_visit_schedule(
    master: MasterSeed,
    unit: Unit,
    person_id: str,
    visit: str,
    *,
    permutation_sha256: str | None = None,
) -> dict[str, Any]:
    """The visit-schedule document of one person and visit.

    ``unit`` must come from ``build_units(master, ...)``. ``permutation_sha256`` (the
    SHA-256 of the unit's ``permutation.json``) is computed when not given.
    Schema: ``schedules/schema/visit-schedule.schema.json``.
    """
    _check_inputs(master, unit, person_id, visit)
    study = unit.study
    wave = visit_wave(study, visit)
    if permutation_sha256 is None:
        permutation_sha256 = hashlib.sha256(permutation_json(unit)).hexdigest()

    blocks: list[dict[str, Any]] = []
    for position, plan in enumerate(visit_plan(study, visit), start=1):
        bo = block_order(master, unit, person_id, visit, plan.block)
        validity: dict[str, Any] | None = None
        if plan.block == "validity":
            speech_seed, speech_sha = _speech(master, study, unit.set_name)
            validity = {
                "no_cue_targets": [c.message_id for c in bo.no_cue_targets],
                "speech_list_seed": speech_seed,
                "speech_list_sha256": speech_sha,
            }
        prefix = f"{person_id}-{visit}-{BLOCK_CODE[plan.block]}"
        blocks.append(
            {
                "block": plan.block,
                "position": position,
                "phase": plan.phase,
                "expected_count": plan.count,
                "passes": plan.passes,
                "slot_s": plan.slot_s,
                "seconds": plan.seconds,
                "shared_by": bo.shared_by,
                "order_source": bo.order_source,
                "seed": bo.seed,
                "seed_tokens": None if bo.seed_tokens is None else list(bo.seed_tokens),
                "validity": validity,
                "items": [_item(unit, prefix, i, e) for i, e in enumerate(bo.items, start=1)],
            }
        )

    return {
        "format": VISIT_SCHEDULE_FORMAT,
        "format_version": VISIT_SCHEDULE_FORMAT_VERSION,
        "generator": {"name": "av-schedules", "version": __version__},
        "hidden_answer": True,
        "demo": unit.demo,
        "seed_label": unit.seed_label,
        "study": study,
        "set": unit.set_name,
        "unit_id": unit.unit_id,
        "unit_kind": unit.kind,
        "person_id": person_id,
        "person_slot": person_id.removeprefix(f"{unit.unit_id}-"),
        "visit": visit,
        "wave": wave,
        "swap_w1_w4": unit.swap_w1_w4,
        "family_first": unit.family_first,
        "permutation_json_sha256": permutation_sha256,
        "assessment": assessment_counts(study, visit),
        "dictionary_messages": [c.message_id for c in trained_upto(wave)],
        "blocks": blocks,
    }


def visit_schedule_json(doc: Mapping[str, Any]) -> bytes:
    """Canonical bytes of a schedule document (indent 2, sorted keys, LF, UTF-8)."""
    return dumps_json(doc)


def unit_schedules(master: MasterSeed, unit: Unit) -> dict[str, dict[str, Any]]:
    """Every schedule of a unit, keyed ``<person_id>/<visit>.json``."""
    sha = hashlib.sha256(permutation_json(unit)).hexdigest()
    return {
        f"{person}/{visit}.json": build_visit_schedule(
            master, unit, person, visit, permutation_sha256=sha
        )
        for person in person_ids(unit)
        for visit in study_visits(unit.study)
    }


# ---------------------------------------------------------------------------------------
# Independent rule check (used by the tests; reusable by run sheets and the engine)


@cache
def _expected_pool(study: Study, visit: str, block: str, swap: bool) -> tuple[str, ...] | None:
    """Expected item IDs of one pass of a block (sorted), or None if not checked here."""
    wave = visit_wave(study, visit)
    ids: tuple[str, ...]
    if block in ("atom_menus", "atomic_lessons"):
        ids = new_atoms(study, visit)
    elif block == "message_lessons":
        ids = tuple(c.message_id for c in new_trained(study, visit))
    elif block == "pre_old":
        ids = tuple(c.message_id for c in trained_upto(wave - 1))
    elif block == "trained":
        ids = tuple(c.message_id for c in trained_upto(wave))
    elif block == "novel":
        ids = tuple(
            c.message_id
            for c in cells()
            if c.heldout_set is not None and novel_visit(study, c.heldout_set, swap) == visit
        )
    elif block == "atomic":
        ids = atoms_upto(wave)
    else:
        return None
    return tuple(sorted(ids))


def _item_key(item: Mapping[str, Any]) -> str | None:
    value = item.get("message_id") or item.get("atom_id")
    return value if isinstance(value, str) else None


def check_visit_schedule(doc: Mapping[str, Any]) -> list[str]:
    """Check one schedule document against the visit plan and the matrix.

    Returns a list of problems ``"<unit> <person> <visit>: <rule>: <detail>"`` (empty when
    the schedule is valid); see :func:`visit_schedule_findings` for the rules.
    """
    return [str(f) for f in visit_schedule_findings(doc)]


def visit_schedule_findings(doc: Mapping[str, Any]) -> list[Finding]:
    """Check one schedule document against the visit plan and the matrix.

    Returns one :class:`Finding` per broken rule, naming the unit, person, visit and rule
    (empty when the schedule is valid). Rules: ``blocks`` (block sequence and counts),
    ``order`` (trained -> novel -> atomic consecutive and last before validity; pre-old
    before any selection or teaching block), ``once-per-pass``, ``content`` (expected
    items per block), ``heldout`` (no held-out message in lessons, menus, pre-old or the
    dictionary list), ``slots``, ``seconds``, ``validity`` and ``trial-ids``.
    """
    study: Study = doc["study"]
    visit = str(doc["visit"])
    unit_id, person_id = str(doc.get("unit_id")), str(doc.get("person_id"))
    problems: list[Finding] = []

    def bad(rule: str, detail: str) -> None:
        problems.append(Finding(unit_id, person_id, visit, rule, detail))

    heldout_ids = {c.message_id for c in cells() if c.heldout_set is not None}
    plan = visit_plan(study, visit)
    blocks = list(doc["blocks"])
    names = [b["block"] for b in blocks]
    if names != [p.block for p in plan]:
        bad("blocks", f"block sequence {names} != {[p.block for p in plan]}")

    protected = [i for i, n in enumerate(names) if BLOCK_PHASE.get(n) == "protected"]
    if [names[i] for i in protected] != list(PROTECTED_BATTERY) or (
        protected and protected != list(range(protected[0], protected[0] + 3))
    ):
        bad(
            "order",
            f"protected blocks {[names[i] for i in protected]} are not trained, novel, atomic",
        )
    if "pre_old" in names:
        first_teaching = min(
            (i for i, n in enumerate(names) if BLOCK_PHASE.get(n) in ("selection", "teaching")),
            default=len(names),
        )
        if names.index("pre_old") > first_teaching:
            bad("order", "pre-old block does not precede teaching")
    if "validity" in names and protected and names.index("validity") < protected[-1]:
        bad("order", "validity block before the protected battery")

    swap = bool(doc["swap_w1_w4"])
    seen_ids: set[str] = set()
    prefix = f"{doc.get('person_id')}-{visit}-"
    total = 0
    for p, b in zip(plan, blocks, strict=False):
        name = b["block"]
        items = list(b["items"])
        if p.block != name:
            continue
        if not (b["expected_count"] == p.count == len(items)):
            bad("blocks", f"{name}: {len(items)} items, expected {p.count}")
        if b["passes"] != p.passes:
            bad("blocks", f"{name}: {b['passes']} passes, expected {p.passes}")
        for it in items:
            tid = str(it["trial_id"])
            if tid in seen_ids or not tid.startswith(prefix):
                bad("trial-ids", f"{name}: trial_id {tid!r} duplicated or not prefixed {prefix!r}")
            seen_ids.add(tid)
            if it["slot_s"] != SLOT_S[it["trial_type"]] or it["slot_s"] != p.slot_s:
                bad("slots", f"{name}: {tid} slot {it['slot_s']} s")
        if name in ASSESSMENT_BLOCKS:
            total += sum(int(it["slot_s"]) for it in items)
        if name in NO_HELDOUT_BLOCKS:
            for it in items:
                hidden = (it.get("intended") or {}).get("message_id")
                if it.get("message_id") in heldout_ids or hidden in heldout_ids:
                    bad(
                        "heldout",
                        f"{name}: held-out message {it.get('message_id')} at {it['trial_id']}",
                    )
        expected = _expected_pool(study, visit, name, swap)
        if expected is None:
            continue
        pass_numbers = [int(it["pass"]) for it in items]
        if pass_numbers != sorted(pass_numbers):
            bad("once-per-pass", f"{name}: passes are interleaved")
        for pass_no in range(1, p.passes + 1):
            got = tuple(sorted(str(_item_key(it)) for it in items if it["pass"] == pass_no))
            if got != expected:
                rule = "content" if p.passes == 1 else "once-per-pass"
                bad(rule, f"{name} pass {pass_no}: items {got} != {expected}")

    if any(m in heldout_ids for m in doc.get("dictionary_messages", [])):
        bad("heldout", "held-out message in the dictionary list")
    if doc["assessment"]["assessment_seconds"] != total:
        bad("seconds", f"assessment seconds {total} != {doc['assessment']['assessment_seconds']}")
    if doc["assessment"] != assessment_counts(study, visit):
        bad("seconds", "assessment counts differ from the visit plan")

    for b in blocks:
        if b["block"] != "validity":
            continue
        kinds = [it["trial_type"] for it in b["items"]]
        if kinds.count("no_cue") != VALIDITY_NO_CUE or kinds.count("speech") != VALIDITY_SPEECH:
            bad(
                "validity",
                f"{kinds.count('no_cue')} no-cue and {kinds.count('speech')} speech trials",
            )
        spoken = [it["intended"] for it in b["items"] if it["trial_type"] == "speech"]
        for f in FAMILIES:
            fam = [s for s in spoken if s["family"] == f]
            for role in ("action", "referent"):
                labels = sorted(s[f"semantic_{role}"] for s in fam)
                if labels != sorted(LABELS[f][role]):
                    bad("validity", f"speech {f} {role}s {labels} do not cover each label once")
        no_cue = sorted(
            it["intended"]["message_id"] for it in b["items"] if it["trial_type"] == "no_cue"
        )
        if b["validity"] is None or sorted(b["validity"]["no_cue_targets"]) != no_cue:
            bad("validity", "stored no-cue target list differs from the no-cue trials")
    return problems
