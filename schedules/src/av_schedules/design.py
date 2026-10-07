"""Unit design tables: the Study A batch table and the Study B dyad-slot design table.

A *unit* is one Study A matching batch (the three method books share it) or one Study B
dyad slot (both members share it). Every unit carries the semantic-label permutation,
the H-W1/H-W4 swap flag and the stored atom introduction order; the abstract matrix is
the same for all units.

Units are numbered in their stored order (``sequence``; an ID order, not a generation or
recruitment order) and grouped into blocks of 4 consecutive units; 4 consecutive blocks
form a Latin *cycle*. Each block holds each of 4 grid columns once, in a seeded order (a
permuted block):

* Study A columns: 0, 2 = unswapped, 1, 3 = swapped (H-W1/H-W4 swap).
* Study B columns: (SQ-1, unswapped), (SQ-1, swapped), (SQ-2, unswapped), (SQ-2, swapped).

A unit's grid position is (row = block within cycle, column). The GF(4) Latin squares of
``latin.py`` on that grid shift the semantic labels (action and referent lines use
orthogonal squares, so every semantic message sits at every matrix cell exactly once per
cycle) and set the atom-order factors. See ``schedules/docs/curriculum.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

from .latin import ORDER, CycleSquares
from .matrix import (
    FAMILIES,
    INDICES,
    LABELS,
    ROLES,
    Family,
    Role,
    Study,
    atom_id,
    wave_indices,
)
from .seeds import MasterSeed, derive_seed, stream

SetName = Literal["pilot", "confirmatory"]
SET_NAMES: Final[tuple[SetName, ...]] = ("pilot", "confirmatory")
SET_CODE: Final[dict[SetName, str]] = {"pilot": "P", "confirmatory": "C"}
SPARE_CODE: Final = "S"

BLOCK_SIZE: Final = ORDER
CYCLE_BLOCKS: Final = ORDER
CYCLE_SIZE: Final = BLOCK_SIZE * CYCLE_BLOCKS

# Study A (Protocol constants, Study A planning defaults).
A_BATCHES: Final[dict[SetName, int]] = {"pilot": 3, "confirmatory": 18}
PROFILES: Final[tuple[str, ...]] = ("P1", "P2", "P3")
DESIGNERS: Final[tuple[str, ...]] = ("D1", "D2", "D3")
# Swapped grid columns {1, 3} form a coset of {0, 3} in GF(4) terms (CycleSquares.draw), as
# in Study B, so each semantic message is the immediate novel test once and the delayed
# novel test once in its two H-W1 and two H-W4 placements of a cycle.
A_CELL_SWAP: Final[tuple[bool, ...]] = (False, True, False, True)

# Study B (Study B protocol section 2).
B_DYADS: Final[dict[SetName, int]] = {"pilot": 8, "confirmatory": 64}
B_DEFAULT_SPARES: Final = 8
B_MAX_SPARES: Final = 96  # spare IDs B-S01..B-S96 (two digits)
SQ_ARMS: Final[tuple[str, ...]] = ("SQ-1", "SQ-2")
STRUCTURED_FAMILY: Final[dict[str, Family]] = {"SQ-1": "K", "SQ-2": "Q"}
B_CELLS: Final[tuple[tuple[str, bool], ...]] = (
    ("SQ-1", False),
    ("SQ-1", True),
    ("SQ-2", False),
    ("SQ-2", True),
)

PERMUTATION_LINES: Final[tuple[tuple[Family, Role], ...]] = tuple(
    (f, r) for f in FAMILIES for r in ROLES
)
# GF(4) Latin square L_k (latin.CycleSquares.latin) that shifts each label line: label at
# index i (0-based) = base[i ^ L_k]. Action and referent use orthogonal squares, so every
# semantic message sits at every matrix cell once per cycle; with the column constraint of
# CycleSquares.draw, each message's two H-W1 and two H-W4 cells also fall once in a
# swapped and once in an unswapped Study B column.
PERMUTATION_SQUARE: Final[dict[tuple[Family, Role], int]] = {
    ("K", "action"): 1,
    ("K", "referent"): 3,
    ("Q", "action"): 1,
    ("Q", "referent"): 3,
}
# Study A atom-order track shift per line (the other of L_1, L_3), so that both matrix
# index and semantic label are balanced over atom positions in every cycle.
ORDER_SQUARE: Final[dict[tuple[Family, Role], int]] = {
    ("K", "action"): 3,
    ("K", "referent"): 1,
    ("Q", "action"): 3,
    ("Q", "referent"): 1,
}


@dataclass(frozen=True)
class Permutation:
    """Semantic labels per line, listed at matrix indices 1..4 (line order: PERMUTATION_LINES)."""

    lines: tuple[tuple[str, ...], ...]

    def labels(self, family: str, role: str) -> tuple[str, ...]:
        """Labels at indices 1..4 for one family and role."""
        for (f, r), labels in zip(PERMUTATION_LINES, self.lines, strict=True):
            if f == family and r == role:
                return labels
        raise KeyError((family, role))

    def label(self, family: str, role: str, index: int) -> str:
        return self.labels(family, role)[index - 1]

    def atom_label(self, atom: str) -> str:
        family, rest = atom.split("-", 1)
        role = "action" if rest[0] == "a" else "referent"
        return self.label(family, role, int(rest[1:]))

    def index_of(self, family: str, role: str, label: str) -> int:
        return self.labels(family, role).index(label) + 1


@dataclass(frozen=True)
class Unit:
    """One curriculum unit (fields shared by Study A batches and Study B dyad slots)."""

    study: Study
    set_name: SetName
    unit_id: str
    kind: str  # "batch" (A), "dyad" or "spare" (B)
    sequence: int  # 1-based position in the stored ID order (not a generation/recruitment order)
    block_id: str
    block_position: int  # 1-based position within the block
    cycle: int  # 1-based Latin cycle (4 blocks)
    grid_row: int  # 0-based block within the cycle
    grid_col: int  # 0-based grid column (A: swap cell; B: SQ x swap cell)
    seed: str  # public unit identifier sha256(master|study|unit_id|unit); never seeds draws
    demo: bool  # True when generated from a public DEMO- seed
    seed_label: str  # public master-seed label: the DEMO- seed or sha256:<fingerprint>
    swap_w1_w4: bool
    permutation: Permutation
    family_first: Family
    atom_order: tuple[str, ...]  # all 16 atoms in introduction order
    wave_orders: tuple[tuple[str, ...], ...]  # Study B: atoms per wave 1..3; Study A: ()


@dataclass(frozen=True)
class ABatch(Unit):
    """A Study A matching batch; the three method books share this curriculum."""

    profile: str
    designer: str  # anonymous A1 hand-designer ID


@dataclass(frozen=True)
class BDyadSlot(Unit):
    """A Study B dyad slot; both dyad members share this curriculum."""

    sq_arm: str

    @property
    def structured_family(self) -> Family:
        return STRUCTURED_FAMILY[self.sq_arm]

    @property
    def dictionary_family(self) -> Family:
        return "Q" if self.structured_family == "K" else "K"


# ---------------------------------------------------------------------------------------
# Seeded draws per cycle and per block


@dataclass(frozen=True)
class _Cycle:
    squares: CycleSquares
    perm_bases: tuple[tuple[str, ...], ...]  # per PERMUTATION_LINES
    order_bases: tuple[tuple[int, ...], ...]  # per PERMUTATION_LINES (track index order)

    def permutation(self, row: int, col: int) -> Permutation:
        lines = []
        for line, base in zip(PERMUTATION_LINES, self.perm_bases, strict=True):
            shift = self.squares.latin(PERMUTATION_SQUARE[line], row, col)
            lines.append(tuple(base[i ^ shift] for i in range(ORDER)))
        return Permutation(tuple(lines))


def _draw_cycle(
    master: MasterSeed,
    study: Study,
    token: str,
    order_indices: Mapping[Role, tuple[int, ...]],
) -> _Cycle:
    squares = CycleSquares.draw(stream(master, study, token, "squares"))
    bases: list[tuple[str, ...]] = []
    order_bases: list[tuple[int, ...]] = []
    for family, role in PERMUTATION_LINES:
        s = stream(master, study, token, f"perm:{family}:{role}")
        bases.append(s.permutation(LABELS[family][role]))
        o = stream(master, study, token, f"order:{family}:{role}")
        order_bases.append(o.permutation(order_indices[role]))
    return _Cycle(squares, tuple(bases), tuple(order_bases))


def _block_cells(master: MasterSeed, study: Study, block_id: str, size: int) -> tuple[int, ...]:
    """Cells of one block in seeded order.

    A partial block (``size`` < 4) takes the first cell ``c`` of the seeded order, then its
    diagonal partner ``3 - c`` (in Study B the other SQ arm and swap level; always the
    other family-first value), then further cells in seeded order.
    """
    order = stream(master, study, block_id, "cells").permutation(range(ORDER))
    if size >= ORDER:
        return order
    chosen = [order[0], ORDER - 1 - order[0]]
    chosen += [c for c in order if c not in chosen]
    return tuple(chosen[:size])


@dataclass(frozen=True)
class _Slot:
    sequence: int  # 1-based
    block_id: str
    block_position: int  # 1-based
    cycle: int  # 1-based
    row: int
    col: int


def _slots(master: MasterSeed, study: Study, prefix: str, n: int) -> list[_Slot]:
    out: list[_Slot] = []
    for block0 in range(-(-n // BLOCK_SIZE)):
        size = min(BLOCK_SIZE, n - block0 * BLOCK_SIZE)
        block_id = f"{prefix}-blk{block0 + 1:02d}"
        for pos0, col in enumerate(_block_cells(master, study, block_id, size)):
            cycle0, row = divmod(block0, CYCLE_BLOCKS)
            out.append(
                _Slot(block0 * BLOCK_SIZE + pos0 + 1, block_id, pos0 + 1, cycle0 + 1, row, col)
            )
    return out


# ---------------------------------------------------------------------------------------
# Atom introduction orders


def _rotate(seq: tuple[int, ...], k: int) -> tuple[int, ...]:
    k %= len(seq)
    return seq[k:] + seq[:k]


def _family_order(first: int) -> tuple[Family, Family]:
    return ("K", "Q") if first == 0 else ("Q", "K")


def _role_order(first: int) -> tuple[Role, Role]:
    return ("action", "referent") if first == 0 else ("referent", "action")


def interleave(
    tracks: Mapping[tuple[Family, Role], tuple[int, ...]],
    family_order: tuple[Family, Family],
    role_order: tuple[Role, Role],
) -> tuple[str, ...]:
    """Atom order: for each slot, for each role in ``role_order``, for each family in order.

    Families alternate atom by atom; roles alternate every two atoms.
    """
    n = len(tracks[(family_order[0], role_order[0])])
    out: list[str] = []
    for slot in range(n):
        for role in role_order:
            for family in family_order:
                out.append(atom_id(family, role, tracks[(family, role)][slot]))
    return tuple(out)


def _a_atom_order(cyc: _Cycle, row: int, col: int) -> tuple[Family, tuple[str, ...]]:
    """Study A: each track lists its indices as ``base[k] ^ L_t`` (0-based, t from
    ORDER_SQUARE); family-first and role-first come from the two bits of ``L_2``."""
    ff, rf = cyc.squares.split(row, col)
    fam = _family_order(ff)
    tracks = {}
    for line, base in zip(PERMUTATION_LINES, cyc.order_bases, strict=True):
        shift = cyc.squares.latin(ORDER_SQUARE[line], row, col)
        tracks[line] = tuple(((i - 1) ^ shift) + 1 for i in base)
    return fam[0], interleave(tracks, fam, _role_order(rf))


def _b_wave_orders(cyc: _Cycle, row: int, col: int) -> tuple[Family, tuple[tuple[str, ...], ...]]:
    """Study B: wave-1 role-first and index order from square A; family-first and wave-2
    role-first from the bits of ``L_2``; wave 3 starts with the role that came second in
    wave 2."""
    rf1, rot1 = divmod(cyc.squares.a(row, col), 2)
    ff, rf2 = cyc.squares.split(row, col)
    rf3 = 1 - rf2
    fam = _family_order(ff)
    v1 = {
        line: _rotate(base, rot1)
        for line, base in zip(PERMUTATION_LINES, cyc.order_bases, strict=True)
    }
    v2 = {(f, r): wave_indices(2, r) for f, r in PERMUTATION_LINES}
    v3 = {(f, r): wave_indices(3, r) for f, r in PERMUTATION_LINES}
    waves = (
        interleave(v1, fam, _role_order(rf1)),
        interleave(v2, fam, _role_order(rf2)),
        interleave(v3, fam, _role_order(rf3)),
    )
    return fam[0], waves


# ---------------------------------------------------------------------------------------
# Study A


def build_a_batch_table(master: MasterSeed, set_name: SetName) -> tuple[ABatch, ...]:
    """Study A batches with profile, anonymous A1 designer, swap flag and curriculum design.

    Confirmatory: 18 batches, the full factorial profile (3) x designer (3) x swap (2), in a
    cycle of 16 (blocks 1-4) plus block 5 (2 batches). Columns 1 and 3 are swapped, so each
    block of 4 has 2 swapped batches and the partial block 5 (a diagonal cell pair) one.
    Profiles are dealt by block: each of blocks 1-3 goes to one profile (seeded), block 4 is
    split between two profiles along its diagonal cell pairs (0, 3) and (1, 2), and block 5
    goes to the third profile. So each profile has 6 batches, 3 swapped, and covers a
    complete Latin row plus two cells of one row (label-at-index counts 1 or 2 within every
    profile; K/Q lead 3/3). Within each profile and swap level the designers D1..D3 are
    dealt in a seeded order, so each designer makes 2 books per profile (one swapped, one
    not). Pilot: 3 batches, one per profile, distinct designers, both swap levels.
    """
    code = SET_CODE[set_name]
    prefix = f"A-{code}"
    n = A_BATCHES[set_name]
    slots = _slots(master, "A", prefix, n)
    s = stream(master, "A", prefix, "batch-table")
    order_indices: dict[Role, tuple[int, ...]] = {"action": INDICES, "referent": INDICES}
    cycles: dict[int, _Cycle] = {}

    def cycle_of(number: int) -> _Cycle:
        if number not in cycles:
            token = f"{prefix}-cyc{number:02d}"
            cycles[number] = _draw_cycle(master, "A", token, order_indices)
        return cycles[number]

    swaps = [A_CELL_SWAP[slot.col] for slot in slots]
    if set_name == "confirmatory":
        if n != CYCLE_SIZE + 2:
            raise RuntimeError("the Study A layout assumes 16 + 2 confirmatory batches")
        whole = s.permutation(PROFILES)  # blocks 1-3
        split = s.permutation(PROFILES)  # [0]: block 5; [1]: block 4 cells 0, 3; [2]: 1, 2
        profiles: list[str] = []
        for slot in slots:
            block0 = (slot.sequence - 1) // BLOCK_SIZE
            if block0 < len(PROFILES):
                profiles.append(whole[block0])
            elif block0 == len(PROFILES):
                profiles.append(split[1] if slot.col in (0, ORDER - 1) else split[2])
            else:
                profiles.append(split[0])
        designers = [""] * n
        for p in PROFILES:
            for swap in (False, True):
                idx = [i for i in range(n) if profiles[i] == p and swaps[i] == swap]
                for i, d in zip(idx, s.permutation(DESIGNERS), strict=True):
                    designers[i] = d
    else:
        profiles = list(s.permutation(PROFILES))
        designers = list(s.permutation(DESIGNERS))

    out: list[ABatch] = []
    for i, slot in enumerate(slots):
        cyc = cycle_of(slot.cycle)
        family_first, order = _a_atom_order(cyc, slot.row, slot.col)
        unit_id = f"A-{code}{slot.sequence:02d}"
        out.append(
            ABatch(
                study="A",
                set_name=set_name,
                unit_id=unit_id,
                kind="batch",
                sequence=slot.sequence,
                block_id=slot.block_id,
                block_position=slot.block_position,
                cycle=slot.cycle,
                grid_row=slot.row,
                grid_col=slot.col,
                seed=derive_seed(master, "A", unit_id, "unit"),
                demo=master.demo,
                seed_label=master.label,
                swap_w1_w4=swaps[i],
                permutation=cyc.permutation(slot.row, slot.col),
                family_first=family_first,
                atom_order=order,
                wave_orders=(),
                profile=profiles[i],
                designer=designers[i],
            )
        )
    return tuple(out)


# ---------------------------------------------------------------------------------------
# Study B


def build_b_design_table(
    master: MasterSeed, set_name: SetName, *, spares: int = B_DEFAULT_SPARES
) -> tuple[BDyadSlot, ...]:
    """Study B dyad slots: block, SQ arm, swap flag, permutation and wave atom orders.

    Slots form permuted blocks of 4 that contain each SQ arm x swap cell once (seeded order
    within the block). Confirmatory sets have 64 main slots plus ``spares`` spare slots
    (a multiple of 4, at most 96; spare blocks follow the main blocks); the pilot has 8
    slots and no spares. Pilot and confirmatory slots never share IDs or seeds.
    """
    if spares < 0 or spares % BLOCK_SIZE or spares > B_MAX_SPARES:
        raise ValueError(f"spares must be a multiple of 4 between 0 and {B_MAX_SPARES}")
    if set_name == "pilot":
        spares = 0  # spare slots exist only for the confirmatory set
    code = SET_CODE[set_name]
    prefix = f"B-{code}"
    n_main = B_DYADS[set_name]
    order_indices: dict[Role, tuple[int, ...]] = {r: wave_indices(1, r) for r in ROLES}
    cycles: dict[int, _Cycle] = {}
    out: list[BDyadSlot] = []
    for slot in _slots(master, "B", prefix, n_main + spares):
        if slot.cycle not in cycles:
            token = f"{prefix}-cyc{slot.cycle:02d}"
            cycles[slot.cycle] = _draw_cycle(master, "B", token, order_indices)
        cyc = cycles[slot.cycle]
        sq_arm, swap = B_CELLS[slot.col]
        family_first, waves = _b_wave_orders(cyc, slot.row, slot.col)
        is_spare = slot.sequence > n_main
        unit_id = (
            f"B-{SPARE_CODE}{slot.sequence - n_main:02d}"
            if is_spare
            else f"B-{code}{slot.sequence:02d}"
        )
        out.append(
            BDyadSlot(
                study="B",
                set_name=set_name,
                unit_id=unit_id,
                kind="spare" if is_spare else "dyad",
                sequence=slot.sequence,
                block_id=slot.block_id,
                block_position=slot.block_position,
                cycle=slot.cycle,
                grid_row=slot.row,
                grid_col=slot.col,
                seed=derive_seed(master, "B", unit_id, "unit"),
                demo=master.demo,
                seed_label=master.label,
                swap_w1_w4=swap,
                permutation=cyc.permutation(slot.row, slot.col),
                family_first=family_first,
                atom_order=waves[0] + waves[1] + waves[2],
                wave_orders=waves,
                sq_arm=sq_arm,
            )
        )
    return tuple(out)


def build_units(
    master: MasterSeed, study: Study, set_name: SetName, *, spares: int = B_DEFAULT_SPARES
) -> tuple[Unit, ...]:
    """All units of one study and set (Study A batches or Study B dyad slots)."""
    if study == "A":
        return build_a_batch_table(master, set_name)
    return build_b_design_table(master, set_name, spares=spares)
