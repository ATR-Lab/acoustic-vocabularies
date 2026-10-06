"""Allocation: Study A recruitment waves and learner slots, Study B dyad roles and banks.

Builds on the unit design tables (#29): ``build_a_batch_table`` supplies the batches with
profile, A1 designer and swap flag; ``build_b_design_table`` supplies the dyad slots with
SQ arm, swap flag and permuted blocks. This module adds the person-level allocation.

Study A (protocol section 4)

* Recruitment waves: confirmatory 6 waves of 3 batches, each wave one batch per profile.
  Waves 1-3 and 4-6 each hold every profile x designer pair once; within every wave the
  three A1 designers differ and 1 or 2 batches are swapped (H-W1/H-W4); within each half
  every profile takes each wave position once. Pilot: 1 wave of 3 batches.
* Learner slots ``<unit>-L01``.. ``-L12`` (pilot ``-L06``) in reveal order: waves in order,
  batches in wave order, slots in number order. Each batch has a concealed seeded
  permutation of 4 A1 / 4 A2 / 4 A3 (pilot 2/2/2) over its slots.
* Each batch x method book gets an anonymous book ID ``BK-<set code>-XXXXXX`` (no method
  label; the code alphabet has no A or D). The learner-facing list maps slot -> book ID;
  the restricted key maps book ID -> batch, method and (A1) designer.
* Vacancies after withdrawal are not refilled: a slot is consumed when revealed.

Study B (protocol section 2)

* Dyad slots, SQ arm and swap flag come from the design table (permuted blocks of 4).
* Member slots ``<unit>-M1`` (first to finish screening) and ``-M2``. Roles: in every
  pair of consecutive blocks, two of the four SQ x swap cells have M1 active in the first
  block and the other two in the second, so M1/M2 are active 50/50 in every cell, every
  block holds 2/2, and every two blocks are balanced per cell.
* Bank IDs are placeholders ``bank-<set code><nnn>`` by dyad-slot sequence (confirmatory
  ``bank-C001``..``bank-C072`` including spares, pilot ``bank-P001``..``bank-P008``).
* Profile-menu presentation order (V1, section 5.1): consecutive groups of 6 dyad slots
  hold all 6 orders of P1, P2, P3, as two 3 x 3 Latin squares, so every group of 3 has
  each profile once at each position.

All draws use ``SeedStream`` over ``derive_seed(master, study, token, purpose)`` with
purposes ``alloc:*``. Tokens contain the set code, so pilot and confirmatory draws never
share a derived seed.
"""

from __future__ import annotations

import hashlib
import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from . import __version__
from .design import (
    B_CELLS,
    B_DEFAULT_SPARES,
    DESIGNERS,
    PROFILES,
    SET_CODE,
    SQ_ARMS,
    ABatch,
    BDyadSlot,
    SetName,
    build_a_batch_table,
    build_b_design_table,
)
from .output import table_csv
from .seeds import MasterSeed, SeedStream, derive_seed, stream

GENERATOR: Final = "av-schedules"

A_METHODS: Final[tuple[str, ...]] = ("A1", "A2", "A3")
A_LEARNERS_PER_BOOK: Final[dict[SetName, int]] = {"pilot": 2, "confirmatory": 4}
A_WAVES: Final[dict[SetName, int]] = {"pilot": 1, "confirmatory": 6}
BOOK_PREFIX: Final = "BK"
# No A or D (method and designer codes), no confusable I, L, O, S, Z, U, E, 0-3.
BOOK_ALPHABET: Final = "BCFGHJKMNPQRTVWXY456789"
BOOK_CODE_LENGTH: Final = 6

B_ROLES: Final[tuple[str, ...]] = ("active", "yoked")
B_MEMBERS: Final[tuple[int, ...]] = (1, 2)
BANK_PREFIX: Final = "bank"
MENU_GROUP: Final = 6


def allocation_seed(master: MasterSeed) -> str:
    """Value for the apparatus manifest's ``allocation_seed``: ``sha256:<fingerprint>``.

    The fingerprint is the SHA-256 of the master seed; the seed itself is never recorded.
    """
    return f"sha256:{master.fingerprint}"


def require_distinct_seeds(pilot: MasterSeed, confirmatory: MasterSeed) -> None:
    """Raise ``ValueError`` if the pilot and confirmatory master seeds are the same."""
    if pilot.fingerprint == confirmatory.fingerprint:
        raise ValueError("pilot and confirmatory lists must use different master seeds")


def a_slot_ids(unit_id: str, set_name: SetName) -> tuple[str, ...]:
    """Learner slot IDs of a Study A batch: ``<unit>-L01``..``-L12`` (pilot ``-L06``)."""
    n = A_LEARNERS_PER_BOOK[set_name] * len(A_METHODS)
    return tuple(f"{unit_id}-L{i:02d}" for i in range(1, n + 1))


def b_slot_ids(unit_id: str) -> tuple[str, str]:
    """Member slot IDs of a Study B dyad slot: ``<unit>-M1`` (first screened), ``-M2``."""
    return (f"{unit_id}-M1", f"{unit_id}-M2")


def bank_id(set_name: SetName, sequence: int) -> str:
    """Placeholder bank ID for the dyad slot at ``sequence`` (``bank-C001``)."""
    return f"{BANK_PREFIX}-{SET_CODE[set_name]}{sequence:03d}"


# ---------------------------------------------------------------------------------------
# Study A


@dataclass(frozen=True)
class ASlot:
    """One learner slot (learner-facing: no method, no designer)."""

    slot_id: str  # A-C07-L03
    unit_id: str
    slot: str  # L03
    order: int  # 1-based reveal order within the set
    wave: int  # 1-based recruitment wave
    wave_position: int  # 1-based position of the batch within its wave
    profile: str
    book_id: str


@dataclass(frozen=True)
class ABook:
    """One method book of a batch (restricted key entry)."""

    book_id: str
    unit_id: str
    method: str  # A1, A2, A3
    designer: str | None  # A1 designer ID; None for A2 and A3
    slots: tuple[str, ...]  # slot IDs assigned to this book, in slot order


@dataclass(frozen=True)
class AAllocation:
    """Study A allocation of one set."""

    set_name: SetName
    demo: bool
    seed_label: str
    allocation_seed: str
    design_table_sha256: str
    batches: tuple[ABatch, ...]  # design-table order
    waves: tuple[tuple[str, ...], ...]  # unit IDs per wave, in recruitment order
    slots: tuple[ASlot, ...]  # reveal order
    books: tuple[ABook, ...]  # design-table order, then A1, A2, A3

    def method_of(self, slot_id: str) -> str:
        """Method of a slot (restricted: for checks and unmasking only)."""
        for book in self.books:
            if slot_id in book.slots:
                return book.method
        raise KeyError(slot_id)


def _split_ok(bits: tuple[int, ...]) -> bool:
    """3 x 3 swap split: rows, columns and cyclic diagonals hold 1 or 2 ones; total 4-5."""
    x = [bits[3 * j : 3 * j + 3] for j in range(3)]
    lines = [list(row) for row in x]
    lines += [[x[j][k] for j in range(3)] for k in range(3)]
    lines += [[x[j][(r + j) % 3] for j in range(3)] for r in range(3)]
    return all(sum(line) in (1, 2) for line in lines) and sum(bits) in (4, 5)


SWAP_SPLITS: Final[tuple[tuple[int, ...], ...]] = tuple(
    bits for bits in itertools.product((0, 1), repeat=9) if _split_ok(bits)
)


def _a_waves(
    master: MasterSeed, set_name: SetName, batches: Sequence[ABatch]
) -> tuple[tuple[ABatch, ...], ...]:
    """Recruitment waves (see module docstring)."""
    s = stream(master, "A", f"A-{SET_CODE[set_name]}", "alloc:waves")
    if set_name == "pilot":
        if sorted(b.profile for b in batches) != sorted(PROFILES):
            raise ValueError("the pilot needs one batch per profile")
        return (s.permutation(batches),)
    by_cell = {(b.profile, b.designer, b.swap_w1_w4): b for b in batches}
    if len(batches) != 18 or len(by_cell) != 18:
        raise ValueError("confirmatory batches must be the profile x designer x swap factorial")
    profiles = s.permutation(PROFILES)
    designers = s.permutation(DESIGNERS)
    # Pattern r pairs profiles[j] with designers[(r + j) % 3] (a cyclic Latin square).
    # split[3 * j + k]: swap level of (profiles[j], designers[k]) in waves 1-3.
    split = SWAP_SPLITS[s.randbelow(len(SWAP_SPLITS))]
    waves: list[tuple[ABatch, ...]] = []
    for half in (0, 1):
        patterns = s.permutation(range(3))
        base = s.permutation(range(3))  # profile positions within the wave
        shifts = s.permutation(range(3))
        for w, r in enumerate(patterns):
            members = []
            for j in range(3):
                k = (r + j) % 3
                level = split[3 * j + k] if half == 0 else 1 - split[3 * j + k]
                members.append(by_cell[(profiles[j], designers[k], bool(level))])
            order = [base[(i + shifts[w]) % 3] for i in range(3)]
            waves.append(tuple(members[j] for j in order))
    return tuple(waves)


def _book_code(seed: str) -> str:
    s = SeedStream(seed)
    return "".join(BOOK_ALPHABET[s.randbelow(len(BOOK_ALPHABET))] for _ in range(BOOK_CODE_LENGTH))


def _design_sha256(units: Sequence[ABatch] | Sequence[BDyadSlot]) -> str:
    return hashlib.sha256(table_csv(units)).hexdigest()


def build_a_allocation(master: MasterSeed, set_name: SetName) -> AAllocation:
    """Study A waves, learner slots, book IDs and the restricted book key for one set."""
    code = SET_CODE[set_name]
    batches = build_a_batch_table(master, set_name)
    waves = _a_waves(master, set_name, batches)
    per_book = A_LEARNERS_PER_BOOK[set_name]

    # Anonymous book IDs, unique within the set; the set code keeps sets disjoint.
    book_ids: dict[tuple[str, str], str] = {}
    taken: set[str] = set()
    for b in batches:
        for method in A_METHODS:
            attempt = 0
            while True:
                seed = derive_seed(master, "A", b.unit_id, f"alloc:book:{method}:{attempt}")
                candidate = f"{BOOK_PREFIX}-{code}-{_book_code(seed)}"
                if candidate not in taken:
                    break
                attempt += 1
            taken.add(candidate)
            book_ids[(b.unit_id, method)] = candidate

    methods_of: dict[str, tuple[str, ...]] = {}
    for b in batches:
        pool = [m for m in A_METHODS for _ in range(per_book)]
        methods_of[b.unit_id] = stream(master, "A", b.unit_id, "alloc:slots").permutation(pool)

    slots: list[ASlot] = []
    for w, wave in enumerate(waves, start=1):
        for pos, b in enumerate(wave, start=1):
            for slot_id, method in zip(
                a_slot_ids(b.unit_id, set_name), methods_of[b.unit_id], strict=True
            ):
                slots.append(
                    ASlot(
                        slot_id=slot_id,
                        unit_id=b.unit_id,
                        slot=slot_id.rsplit("-", 1)[1],
                        order=len(slots) + 1,
                        wave=w,
                        wave_position=pos,
                        profile=b.profile,
                        book_id=book_ids[(b.unit_id, method)],
                    )
                )

    books = tuple(
        ABook(
            book_id=book_ids[(b.unit_id, method)],
            unit_id=b.unit_id,
            method=method,
            designer=b.designer if method == "A1" else None,
            slots=tuple(
                sid
                for sid, m in zip(
                    a_slot_ids(b.unit_id, set_name), methods_of[b.unit_id], strict=True
                )
                if m == method
            ),
        )
        for b in batches
        for method in A_METHODS
    )
    return AAllocation(
        set_name=set_name,
        demo=master.demo,
        seed_label=master.label,
        allocation_seed=allocation_seed(master),
        design_table_sha256=_design_sha256(batches),
        batches=batches,
        waves=tuple(tuple(b.unit_id for b in wave) for wave in waves),
        slots=tuple(slots),
        books=books,
    )


# ---------------------------------------------------------------------------------------
# Study B


@dataclass(frozen=True)
class BMember:
    slot_id: str  # B-C01-M1
    member: int  # 1 = first to finish screening, 2 = second
    role: str  # active or yoked


@dataclass(frozen=True)
class BDyad:
    """One Study B dyad slot with its concealed allocation."""

    unit_id: str
    kind: str  # dyad or spare
    order: int  # design-table sequence (reveal order of main dyad slots)
    block_id: str
    block_position: int
    sq_arm: str
    structured_family: str
    swap_w1_w4: bool
    members: tuple[BMember, BMember]
    bank_id: str
    profile_menu_order: tuple[str, str, str]

    @property
    def active_member(self) -> int:
        return next(m.member for m in self.members if m.role == "active")


@dataclass(frozen=True)
class BAllocation:
    """Study B allocation of one set (main dyad slots, then spares)."""

    set_name: SetName
    demo: bool
    seed_label: str
    allocation_seed: str
    design_table_sha256: str
    dyads: tuple[BDyad, ...]


def _menu_orders(master: MasterSeed, set_name: SetName, n: int) -> list[tuple[str, str, str]]:
    """Profile-menu orders for ``n`` slots in sequence order (groups of 6, see docstring)."""
    out: list[tuple[str, str, str]] = []
    for g in range(-(-n // MENU_GROUP)):
        s = stream(master, "B", f"B-{SET_CODE[set_name]}-menu{g + 1:02d}", "alloc:menu")
        a, b, c = s.permutation(PROFILES)
        squares = []
        for base in ((a, b, c), (a, c, b)):
            squares.append([base[k:] + base[:k] for k in s.permutation(range(3))])
        first = s.randbelow(2)
        group = squares[first] + squares[1 - first]
        out.extend((p[0], p[1], p[2]) for p in group)
    return out[:n]


def _m1_active(master: MasterSeed, slots: Sequence[BDyadSlot]) -> dict[str, bool]:
    """Whether member 1 is active, per unit ID (block pairs, see module docstring)."""
    blocks: dict[str, list[BDyadSlot]] = {}
    for u in slots:
        blocks.setdefault(u.block_id, []).append(u)
    out: dict[str, bool] = {}
    for kind in ("dyad", "spare"):
        ids = [bid for bid, members in blocks.items() if members[0].kind == kind]
        for i in range(0, len(ids), 2):
            pair = ids[i : i + 2]
            cells = stream(master, "B", pair[0], "alloc:roles").permutation(range(len(B_CELLS)))
            first = set(cells[: len(B_CELLS) // 2])
            for n, bid in enumerate(pair):
                for u in blocks[bid]:
                    out[u.unit_id] = (u.grid_col in first) == (n == 0)
    return out


def build_b_allocation(
    master: MasterSeed, set_name: SetName, *, spares: int = B_DEFAULT_SPARES
) -> BAllocation:
    """Study B roles by member slot, bank IDs and profile-menu orders for one set."""
    slots = build_b_design_table(master, set_name, spares=spares)
    m1_active = _m1_active(master, slots)
    menus = _menu_orders(master, set_name, len(slots))
    dyads = []
    for u, menu in zip(slots, menus, strict=True):
        roles = ("active", "yoked") if m1_active[u.unit_id] else ("yoked", "active")
        m1, m2 = b_slot_ids(u.unit_id)
        dyads.append(
            BDyad(
                unit_id=u.unit_id,
                kind=u.kind,
                order=u.sequence,
                block_id=u.block_id,
                block_position=u.block_position,
                sq_arm=u.sq_arm,
                structured_family=u.structured_family,
                swap_w1_w4=u.swap_w1_w4,
                members=(BMember(m1, 1, roles[0]), BMember(m2, 2, roles[1])),
                bank_id=bank_id(set_name, u.sequence),
                profile_menu_order=menu,
            )
        )
    return BAllocation(
        set_name=set_name,
        demo=master.demo,
        seed_label=master.label,
        allocation_seed=allocation_seed(master),
        design_table_sha256=_design_sha256(slots),
        dyads=tuple(dyads),
    )


# ---------------------------------------------------------------------------------------
# Checks (reused by the run-sheet validation, #32)


def check_a_allocation(alloc: AAllocation) -> list[str]:
    """Count and balance checks for a Study A allocation; returns problems (empty = pass)."""
    problems: list[str] = []
    set_name = alloc.set_name
    per_book = A_LEARNERS_PER_BOOK[set_name]
    n_batches = len(alloc.batches)
    expected_slots = n_batches * per_book * len(A_METHODS)
    if len(alloc.slots) != expected_slots:
        problems.append(f"{len(alloc.slots)} slots, expected {expected_slots}")
    if len({s.slot_id for s in alloc.slots}) != len(alloc.slots):
        problems.append("duplicate slot IDs")
    if [s.order for s in alloc.slots] != list(range(1, len(alloc.slots) + 1)):
        problems.append("reveal order is not 1..n")
    per_profile = {p: sum(1 for b in alloc.batches if b.profile == p) for p in PROFILES}
    if len(set(per_profile.values())) != 1:
        problems.append(f"batches per profile {per_profile}")
    if len(alloc.waves) != A_WAVES[set_name]:
        problems.append(f"{len(alloc.waves)} waves, expected {A_WAVES[set_name]}")
    profile_of = {b.unit_id: b.profile for b in alloc.batches}
    designer_of = {b.unit_id: b.designer for b in alloc.batches}
    for w, wave in enumerate(alloc.waves, start=1):
        if sorted(profile_of[u] for u in wave) != sorted(PROFILES):
            problems.append(f"wave {w} does not hold one batch per profile")
        if set_name == "confirmatory" and len({designer_of[u] for u in wave}) != 3:
            problems.append(f"wave {w} repeats an A1 designer")
    if sorted(u for wave in alloc.waves for u in wave) != sorted(profile_of):
        problems.append("waves do not cover every batch once")
    for b in alloc.batches:
        books = [k for k in alloc.books if k.unit_id == b.unit_id]
        counts = sorted((k.method, len(k.slots)) for k in books)
        if counts != [(m, per_book) for m in A_METHODS]:
            problems.append(f"{b.unit_id}: book slot counts {counts}")
    book_ids = [k.book_id for k in alloc.books]
    if len(set(book_ids)) != len(book_ids):
        problems.append("duplicate book IDs")
    return problems


def check_b_allocation(alloc: BAllocation) -> list[str]:
    """Count and balance checks for a Study B allocation; returns problems (empty = pass)."""
    problems: list[str] = []
    main = [d for d in alloc.dyads if d.kind == "dyad"]
    spare = [d for d in alloc.dyads if d.kind == "spare"]
    for scope, members in (("main", main), ("spares", spare)):
        if not members:
            continue
        for arm in SQ_ARMS:
            in_arm = [d for d in members if d.sq_arm == arm]
            if len(in_arm) * 2 != len(members):
                problems.append(f"{scope}: {arm} has {len(in_arm)} of {len(members)} dyads")
            swapped = sum(1 for d in in_arm if d.swap_w1_w4)
            if swapped * 2 != len(in_arm):
                problems.append(f"{scope}: {arm} swap split {swapped}/{len(in_arm) - swapped}")
            for swap in (False, True):
                cell = [d for d in in_arm if d.swap_w1_w4 == swap]
                m1 = sum(1 for d in cell if d.active_member == 1)
                if abs(2 * m1 - len(cell)) > 1:  # 50/50 (odd cells: as even as possible)
                    problems.append(f"{scope}: {arm} swap={int(swap)} M1 active {m1}/{len(cell)}")
    for d in alloc.dyads:
        if sorted(m.role for m in d.members) != sorted(B_ROLES):
            problems.append(f"{d.unit_id}: roles {[m.role for m in d.members]}")
        if sorted(d.profile_menu_order) != sorted(PROFILES):
            problems.append(f"{d.unit_id}: menu order {d.profile_menu_order}")
    banks = [d.bank_id for d in alloc.dyads]
    if len(set(banks)) != len(banks):
        problems.append("duplicate bank IDs")
    full = len(alloc.dyads) // MENU_GROUP * MENU_GROUP
    for g in range(0, full, MENU_GROUP):
        orders = {d.profile_menu_order for d in alloc.dyads[g : g + MENU_GROUP]}
        if len(orders) != MENU_GROUP:
            problems.append(f"menu group starting at {g + 1} repeats an order")
    return problems


def generator_info() -> dict[str, str]:
    return {"name": GENERATOR, "version": __version__}
