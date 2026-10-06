"""The abstract 4x4 message matrix and everything derived from it.

``MATRIX`` is the single source constant (Protocol constants, "Inventory and growth").
Every other structure here (trained waves, held-out sets, index introduction waves,
component availability, Study A novel use and Study B first-novel visits) is derived
from it, so a change to the matrix propagates everywhere and is caught by the tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import Final, Literal

Family = Literal["K", "Q"]
Role = Literal["action", "referent"]
Study = Literal["A", "B"]

FAMILIES: Final[tuple[Family, ...]] = ("K", "Q")
ROLES: Final[tuple[Role, ...]] = ("action", "referent")
INDICES: Final[tuple[int, ...]] = (1, 2, 3, 4)
WAVES: Final[tuple[int, ...]] = (1, 2, 3)

ROLE_PREFIX: Final[dict[Role, str]] = {"action": "a", "referent": "r"}

# Semantic labels per family and role (Protocol constants; planning ontology.csv).
# They are permuted onto matrix indices per unit; the tuple order carries no index.
LABELS: Final[dict[Family, dict[Role, tuple[str, ...]]]] = {
    "K": {
        "action": ("ADD_ONE", "REMOVE_ONE", "FLIP_CARD", "ALIGN_ARROW"),
        "referent": ("A", "B", "C", "D"),
    },
    "Q": {
        "action": ("SCAN", "TAG", "CLOSE", "QUARANTINE"),
        "referent": ("E", "F", "G", "H"),
    },
}

# The matrix per family: rows a1..a4 (action index), columns r1..r4 (referent index).
# "Train Vn" = trained message introduced at wave n; "H-x" = held-out set x.
MATRIX: Final[tuple[tuple[str, str, str, str], ...]] = (
    ("Train V1", "H-V1", "Train V2", "H-W1"),
    ("H-W4", "Train V1", "H-V2", "Train V3"),
    ("Train V2", "H-W1", "Train V2", "Train V3"),
    ("H-V3", "Train V3", "Train V3", "H-W4"),
)

HELDOUT_SETS: Final[tuple[str, ...]] = ("H-V1", "H-V2", "H-V3", "H-W1", "H-W4")

# Assessment visits (planning assessment-schedule.csv naming).
A_VISITS: Final[tuple[str, ...]] = ("D0", "D7")
B_VISITS: Final[tuple[str, ...]] = ("V1", "V2", "V3", "W1", "W4")
# Inventory wave available at each B visit (W1/W4 follow V3).
B_VISIT_WAVE: Final[dict[str, int]] = {"V1": 1, "V2": 2, "V3": 3, "W1": 3, "W4": 3}
# Study A uses H-W1 for immediate (D0) and H-W4 for delayed (D7) novelty; the other
# held-out sets are unused in A (Study A protocol section 4).
A_IMMEDIATE_SET: Final = "H-W1"
A_DELAYED_SET: Final = "H-W4"
A_NOVEL_DEFAULT: Final[dict[str, str]] = {A_IMMEDIATE_SET: "immediate", A_DELAYED_SET: "delayed"}
A_NOVEL_VISIT: Final[dict[str, str]] = {"immediate": "D0", "delayed": "D7"}
UNUSED: Final = "unused"


@dataclass(frozen=True, order=True)
class Cell:
    """One whole message: (action index, referent index) in one family."""

    family: Family
    action_index: int
    referent_index: int

    @property
    def status(self) -> str:
        return MATRIX[self.action_index - 1][self.referent_index - 1]

    @property
    def message_id(self) -> str:
        return message_id(self.family, self.action_index, self.referent_index)

    @property
    def action_atom(self) -> str:
        return atom_id(self.family, "action", self.action_index)

    @property
    def referent_atom(self) -> str:
        return atom_id(self.family, "referent", self.referent_index)

    @property
    def training_wave(self) -> int | None:
        """Wave at which the message is trained (1..3), or None if held out."""
        if self.status.startswith("Train V"):
            return int(self.status.removeprefix("Train V"))
        return None

    @property
    def heldout_set(self) -> str | None:
        """Held-out set name (``H-V1`` .. ``H-W4``), or None if trained."""
        return self.status if self.status.startswith("H-") else None

    @property
    def components_available_wave(self) -> int:
        """First wave at which both component atoms are introduced."""
        waves = index_waves()
        return max(waves["action"][self.action_index], waves["referent"][self.referent_index])

    @property
    def b_first_novel_visit_default(self) -> str | None:
        """Study B test visit of this held-out message before any W1/W4 swap."""
        hs = self.heldout_set
        return None if hs is None else hs.removeprefix("H-")

    @property
    def a_novel_default(self) -> str | None:
        """Study A use of this held-out message before any swap: immediate/delayed/unused."""
        hs = self.heldout_set
        return None if hs is None else A_NOVEL_DEFAULT.get(hs, UNUSED)


def atom_id(family: str, role: Role, index: int) -> str:
    """Atom identifier, e.g. ``K-a1`` or ``Q-r3``."""
    return f"{family}-{ROLE_PREFIX[role]}{index}"


def parse_atom_id(value: str) -> tuple[Family, Role, int]:
    """Inverse of :func:`atom_id`."""
    for family in FAMILIES:
        for role in ROLES:
            for index in INDICES:
                if value == atom_id(family, role, index):
                    return family, role, index
    raise ValueError(f"not an atom id: {value!r}")


def message_id(family: str, action_index: int, referent_index: int) -> str:
    """Message identifier as in planning ``curriculum.csv``, e.g. ``K-a1-r2``."""
    return f"{family}-a{action_index}-r{referent_index}"


@cache
def cells() -> tuple[Cell, ...]:
    """All 32 cells in planning order: family, action index, referent index."""
    return tuple(Cell(f, a, r) for f in FAMILIES for a in INDICES for r in INDICES)


def family_cells(family: str) -> tuple[Cell, ...]:
    return tuple(c for c in cells() if c.family == family)


def trained_cells(wave: int | None = None) -> tuple[Cell, ...]:
    """Trained cells (both families), optionally restricted to one training wave."""
    return tuple(
        c
        for c in cells()
        if c.training_wave is not None and (wave is None or c.training_wave == wave)
    )


def heldout_cells(heldout_set: str | None = None) -> tuple[Cell, ...]:
    """Held-out cells (both families), optionally restricted to one held-out set."""
    return tuple(
        c
        for c in cells()
        if c.heldout_set is not None and (heldout_set is None or c.heldout_set == heldout_set)
    )


@cache
def index_waves() -> dict[Role, dict[int, int]]:
    """Wave at which each matrix index is introduced, derived from the matrix.

    An index is introduced with the first trained message that uses it.
    """
    out: dict[Role, dict[int, int]] = {"action": {}, "referent": {}}
    for i in INDICES:
        out["action"][i] = min(
            w for r in INDICES if (w := Cell("K", i, r).training_wave) is not None
        )
        out["referent"][i] = min(
            w for a in INDICES if (w := Cell("K", a, i).training_wave) is not None
        )
    return out


def atoms() -> tuple[str, ...]:
    """All 16 atom IDs in canonical order (family, role, index)."""
    return tuple(atom_id(f, role, i) for f in FAMILIES for role in ROLES for i in INDICES)


def atom_wave(atom: str) -> int:
    _, role, index = parse_atom_id(atom)
    return index_waves()[role][index]


def wave_atoms(wave: int) -> tuple[str, ...]:
    """Atoms first introduced at ``wave`` (canonical order)."""
    return tuple(a for a in atoms() if atom_wave(a) == wave)


def wave_indices(wave: int, role: Role) -> tuple[int, ...]:
    """Matrix indices of ``role`` first introduced at ``wave``."""
    return tuple(i for i in INDICES if index_waves()[role][i] == wave)


def novel_visit(study: Study, heldout_set: str, swap_w1_w4: bool) -> str:
    """Visit at which a held-out set is tested for one unit, after the W1/W4 swap.

    Study A returns ``D0``/``D7`` or ``unused``; Study B returns ``V1``..``W4``.
    """
    if heldout_set not in HELDOUT_SETS:
        raise ValueError(f"unknown held-out set {heldout_set!r}")
    effective = heldout_set
    if swap_w1_w4 and heldout_set in (A_IMMEDIATE_SET, A_DELAYED_SET):
        effective = A_DELAYED_SET if heldout_set == A_IMMEDIATE_SET else A_IMMEDIATE_SET
    if study == "A":
        use = A_NOVEL_DEFAULT.get(effective, UNUSED)
        return A_NOVEL_VISIT.get(use, UNUSED)
    return effective.removeprefix("H-")
