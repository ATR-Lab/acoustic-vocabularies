"""Unit design tables: the Study A batch table and the Study B dyad-slot design table.

A *unit* is one Study A matching batch (the three method books share it) or one Study B
dyad slot (both members share it). Every unit carries the semantic-label permutation,
the H-W1/H-W4 swap flag and the stored atom introduction order; the abstract matrix is
the same for all units.

Units are numbered in their stored order (``sequence``) and grouped into blocks of 4
consecutive units; 4 consecutive blocks form a Latin *cycle*. Each block holds each of
4 design cells once, in a seeded order (a permuted block):

* Study A cells: 0, 1 = unswapped, 2, 3 = swapped (H-W1/H-W4 swap).
* Study B cells: (SQ-1, unswapped), (SQ-1, swapped), (SQ-2, unswapped), (SQ-2, swapped).

A unit's grid position is (row = block within cycle, column = cell). Latin squares on
that grid (``latin.py``) assign semantic-permutation rotations and atom-order factors.
See ``schedules/docs/curriculum.md`` for the balancing argument.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

from .latin import ORDER, CycleSquares, CyclicSquare
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
A_CELL_SWAP: Final[tuple[bool, ...]] = (False, False, True, True)

# Study B (Study B protocol section 2).
B_DYADS: Final[dict[SetName, int]] = {"pilot": 8, "confirmatory": 64}
B_DEFAULT_SPARES: Final = 8
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
    sequence: int  # 1-based position in the set's stored order
    block_id: str
    block_position: int  # 1-based position within the block
    cycle: int  # 1-based Latin cycle (4 blocks)
    grid_row: int  # 0-based block within the cycle
    grid_col: int  # 0-based design cell
    seed: str  # unit root seed: sha256(master|study|unit_id|unit)
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
    perm_squares: tuple[CyclicSquare, ...]  # per PERMUTATION_LINES
    order_bases: tuple[tuple[int, ...], ...]  # per PERMUTATION_LINES (track index order)

    def permutation(self, row: int, col: int) -> Permutation:
        lines = []
        for base, square in zip(self.perm_bases, self.perm_squares, strict=True):
            rot = square.value(row, col)
            lines.append(tuple(base[(i + rot) % ORDER] for i in range(ORDER)))
        return Permutation(tuple(lines))


def _draw_cycle(
    master: MasterSeed,
    study: Study,
    token: str,
    order_indices: Mapping[Role, tuple[int, ...]],
) -> _Cycle:
    squares = CycleSquares.draw(stream(master, study, token, "squares"))
    bases: list[tuple[str, ...]] = []
    perm_squares: list[CyclicSquare] = []
    order_bases: list[tuple[int, ...]] = []
    for family, role in PERMUTATION_LINES:
        s = stream(master, study, token, f"perm:{family}:{role}")
        bases.append(s.permutation(LABELS[family][role]))
        perm_squares.append(CyclicSquare.draw(s))
        o = stream(master, study, token, f"order:{family}:{role}")
        order_bases.append(o.permutation(order_indices[role]))
    return _Cycle(squares, tuple(bases), tuple(perm_squares), tuple(order_bases))


def _block_cells(master: MasterSeed, study: Study, block_id: str, size: int) -> tuple[int, ...]:
    """Cells of one block in seeded order.

    A partial block (``size`` < 4) takes the first cell ``c`` of the seeded order, then its
    diagonal partner ``3 - c`` (other swap level; in Study B also the other SQ arm, and
    always the other family-first value), then further cells in seeded order.
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
    """Study A: index rotation from square A; family-first and role-first from square B."""
    rot = cyc.squares.a(row, col)
    ff, rf = cyc.squares.split(row, col)
    fam = _family_order(ff)
    tracks = {
        line: _rotate(base, rot)
        for line, base in zip(PERMUTATION_LINES, cyc.order_bases, strict=True)
    }
    return fam[0], interleave(tracks, fam, _role_order(rf))


def _b_wave_orders(cyc: _Cycle, row: int, col: int) -> tuple[Family, tuple[tuple[str, ...], ...]]:
    """Study B: wave-1 role-first and index order from square A; family-first and wave-2
    role-first from square B; wave 3 starts with the role that came second in wave 2."""
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

    Confirmatory: 18 batches in 5 blocks (4 + 4 + 4 + 4 + 2); every block holds equal
    numbers of unswapped and swapped batches. Profiles are dealt by block: three profiles
    take one whole block each, the fourth block is split between two profiles along its
    diagonal cell pairs (0, 3) and (1, 2), and the last block goes to the third profile.
    So each profile has 6 batches, 3 swapped, and its batches cover a complete Latin row
    plus two cells of one row (label-at-index counts 1 or 2 within every profile). Within
    each profile and swap level the designers D1..D3 are dealt in a seeded order, so each
    designer makes 2 books per profile (one swapped, one not). K and Q each lead the atom
    order of 9 batches (3 per profile). Pilot: 3 batches, one per profile, distinct
    designers, at least one swapped and one unswapped batch.
    """
    code = SET_CODE[set_name]
    prefix = f"A-{code}"
    n = A_BATCHES[set_name]
    slots = _slots(master, "A", prefix, n)
    s = stream(master, "A", prefix, "batch-table")
    if set_name == "confirmatory":
        assert n == 4 * BLOCK_SIZE + 2
        whole = s.permutation(PROFILES)  # blocks 1-3
        split = s.permutation(PROFILES)  # [0]: block 5; [1]: block 4 cells 0, 3; [2]: cells 1, 2

        def profile_of(slot: _Slot) -> str:
            block0 = (slot.sequence - 1) // BLOCK_SIZE
            if block0 < len(PROFILES):
                return whole[block0]
            if block0 == len(PROFILES):
                return split[1] if slot.col in (0, ORDER - 1) else split[2]
            return split[0]

        profiles = [profile_of(slot) for slot in slots]
        designers = [""] * n
        for p in PROFILES:
            for swap in (False, True):
                idx = [
                    i
                    for i, sl in enumerate(slots)
                    if profiles[i] == p and A_CELL_SWAP[sl.col] == swap
                ]
                for i, d in zip(idx, s.permutation(DESIGNERS), strict=True):
                    designers[i] = d
        assignment = list(zip(profiles, designers, strict=True))
    else:
        assignment = list(zip(s.permutation(PROFILES), s.permutation(DESIGNERS), strict=True))

    order_indices: dict[Role, tuple[int, ...]] = {"action": INDICES, "referent": INDICES}
    cycles: dict[int, _Cycle] = {}
    out: list[ABatch] = []
    for slot, (profile, designer) in zip(slots, assignment, strict=True):
        if slot.cycle not in cycles:
            token = f"{prefix}-cyc{slot.cycle:02d}"
            cycles[slot.cycle] = _draw_cycle(master, "A", token, order_indices)
        cyc = cycles[slot.cycle]
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
                swap_w1_w4=A_CELL_SWAP[slot.col],
                permutation=cyc.permutation(slot.row, slot.col),
                family_first=family_first,
                atom_order=order,
                wave_orders=(),
                profile=profile,
                designer=designer,
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
    (a multiple of 4; spare blocks follow the main blocks); the pilot has 8 slots and no
    spares. Pilot and confirmatory slots never share IDs or seeds.
    """
    if spares < 0 or spares % BLOCK_SIZE:
        raise ValueError("spares must be a non-negative multiple of 4")
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
