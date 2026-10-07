"""Message grammar: families, roles, atom and message IDs, and the fixed matrix.

Protocol constants ("Inventory and growth"): two component-disjoint families K and
Q, each with four action atoms (`a1`..`a4`) and four referent atoms (`r1`..`r4`). A
whole message is one action atom then one referent atom of the same family, so a
book has 2 x 4 x 4 = 32 legal messages. The abstract matrix below marks 9 trained
and 7 held-out messages per family: 18 trained and 14 held-out in total.

IDs follow the planning curriculum: atom `K-a1`, `Q-r3`; message `K-a1-r2`.
Semantic labels are permuted onto matrix indices per unit (O4.4.1, #29), so the IDs
here carry indices only, never meanings.

The 14 held-out IDs of the matrix are the composer's fixed held-out set: the same in
every unit (only the test visit changes), so callers can add to it but never remove
from it. The schedules package (#29) encodes the same matrix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal, cast

Family = Literal["K", "Q"]
Role = Literal["action", "referent"]

FAMILIES: Final[tuple[Family, ...]] = ("K", "Q")
ROLES: Final[tuple[Role, ...]] = ("action", "referent")
INDICES: Final[tuple[int, ...]] = (1, 2, 3, 4)
TRAINING_WAVES: Final[tuple[int, ...]] = (1, 2, 3)
HELDOUT_SETS: Final[tuple[str, ...]] = ("H-V1", "H-V2", "H-V3", "H-W1", "H-W4")

_ROLE_LETTER: Final[dict[Role, str]] = {"action": "a", "referent": "r"}
_LETTER_ROLE: Final[dict[str, Role]] = {"a": "action", "r": "referent"}
_ATOM_RE: Final = re.compile(r"(K|Q)-([ar])([1-4])")
_MESSAGE_RE: Final = re.compile(r"(K|Q)-a([1-4])-r([1-4])")

MATRIX: Final[tuple[tuple[str, str, str, str], ...]] = (
    ("Train V1", "H-V1", "Train V2", "H-W1"),
    ("H-W4", "Train V1", "H-V2", "Train V3"),
    ("Train V2", "H-W1", "Train V2", "Train V3"),
    ("H-V3", "Train V3", "Train V3", "H-W4"),
)
"""Per family: row = action index 1..4, column = referent index 1..4 (Protocol constants).

`Train Vn` is a message trained from wave n; `H-x` is a message in held-out set x.
"""


class GrammarError(ValueError):
    """An atom or message ID is malformed or not part of the grammar."""


@dataclass(frozen=True, slots=True, order=True)
class AtomRef:
    """One atom position: family, role and matrix index (1..4)."""

    family: Family
    role: Role
    index: int

    def __post_init__(self) -> None:
        if self.family not in FAMILIES:
            raise GrammarError(f"unknown family {self.family!r}; expected one of {FAMILIES}")
        if self.role not in ROLES:
            raise GrammarError(f"unknown role {self.role!r}; expected one of {ROLES}")
        if isinstance(self.index, bool) or self.index not in INDICES:
            raise GrammarError(f"matrix index {self.index!r} is not one of {INDICES}")

    @property
    def atom_id(self) -> str:
        """For example `K-a1` or `Q-r3`."""
        return f"{self.family}-{_ROLE_LETTER[self.role]}{self.index}"


@dataclass(frozen=True, slots=True, order=True)
class MessageRef:
    """One legal message: an action index and a referent index in one family."""

    family: Family
    action_index: int
    referent_index: int

    def __post_init__(self) -> None:
        AtomRef(self.family, "action", self.action_index)
        AtomRef(self.family, "referent", self.referent_index)

    @property
    def message_id(self) -> str:
        """For example `K-a1-r2` (planning curriculum format)."""
        return f"{self.family}-a{self.action_index}-r{self.referent_index}"

    @property
    def action(self) -> AtomRef:
        return AtomRef(self.family, "action", self.action_index)

    @property
    def referent(self) -> AtomRef:
        return AtomRef(self.family, "referent", self.referent_index)

    @property
    def status(self) -> str:
        """Matrix cell: `Train V1`..`Train V3` or `H-V1`..`H-W4`."""
        return MATRIX[self.action_index - 1][self.referent_index - 1]

    @property
    def training_wave(self) -> int | None:
        """Wave (1..3) from which the message is trained, or None if held out."""
        status = self.status
        return int(status.removeprefix("Train V")) if status.startswith("Train V") else None

    @property
    def heldout_set(self) -> str | None:
        """Held-out set (`H-V1`..`H-W4`), or None if the message is trained."""
        status = self.status
        return status if status.startswith("H-") else None

    @property
    def is_heldout(self) -> bool:
        """True for the 7 held-out messages per family in the fixed matrix."""
        return self.heldout_set is not None


def atom_id(family: str, role: str, index: int) -> str:
    """Atom ID from its parts; raises `GrammarError` outside the grammar."""
    return AtomRef(cast(Family, family), cast(Role, role), index).atom_id


def message_id(family: str, action_index: int, referent_index: int) -> str:
    """Message ID from its parts; raises `GrammarError` outside the grammar."""
    return MessageRef(cast(Family, family), action_index, referent_index).message_id


def parse_atom_id(value: str) -> AtomRef:
    """Parse `K-a1` .. `Q-r4`. Anything else raises `GrammarError`."""
    match = _ATOM_RE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise GrammarError(f"not an atom ID: {value!r} (expected e.g. 'K-a1' or 'Q-r3')")
    family, letter, index = match.groups()
    return AtomRef(cast(Family, family), _LETTER_ROLE[letter], int(index))


def parse_message_id(value: str) -> MessageRef:
    """Parse `K-a1-r1` .. `Q-a4-r4`. Anything else raises `GrammarError`."""
    match = _MESSAGE_RE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise GrammarError(f"not a message ID: {value!r} (expected e.g. 'K-a1-r2')")
    family, action_index, referent_index = match.groups()
    return MessageRef(cast(Family, family), int(action_index), int(referent_index))


ATOM_IDS: Final[tuple[str, ...]] = tuple(
    atom_id(f, role, i) for f in FAMILIES for role in ROLES for i in INDICES
)
"""All 16 atom IDs in canonical order: family, role (action first), index."""

MESSAGES: Final[tuple[MessageRef, ...]] = tuple(
    MessageRef(f, a, r) for f in FAMILIES for a in INDICES for r in INDICES
)
"""All 32 legal messages in canonical order: family, action index, referent index."""

TRAINED_MESSAGE_IDS: Final[tuple[str, ...]] = tuple(
    m.message_id for m in MESSAGES if not m.is_heldout
)
"""The 18 trained message IDs of the fixed matrix (9 per family)."""

HELDOUT_MESSAGE_IDS: Final[tuple[str, ...]] = tuple(m.message_id for m in MESSAGES if m.is_heldout)
"""The 14 held-out message IDs of the fixed matrix (7 per family)."""
