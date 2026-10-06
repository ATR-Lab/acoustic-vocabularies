"""Identifiers shared by every generation log: studies, methods, run kinds and slot IDs.

ID formats (all ASCII, no `|`, no `:`, so they are safe in seed keys, JSONL and file
names on every platform):

| ID | Format | Example |
| --- | --- | --- |
| batch | schedules unit ID or `DEMO-...` | `A-P01`, `DEMO-A-01` |
| book | anonymous book ID (schedules book key; store rules) | `BK-C-7QX4MN` |
| bank | schedules bank ID or `DEMO-...` | `bank-C001` |
| proposal slot (A) | `<book>.<atom>.r<round>s<slot>` | `BK-C-7QX4MN.K-a1.r2s3` |
| proposal slot (B) | `<bank>.t<attempt>.<profile>.<atom>.s<slot:02>` | `bank-C001.t1.P2.Q-r4.s07` |
| rating slot | `<batch>.<atom>.r<round>p<position>` | `A-P01.K-a1.r2p5` |

A proposal-slot ID names the anonymous book, never the method. A rating-slot ID names
neither the book nor the method: it is what the rater stations see.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Final, NamedTuple

from av_sound.grammar import ATOM_IDS
from av_sound.store import check_book_id

from av_generation.constants import (
    B_MAX_ATTEMPTS,
    B_SLOTS_PER_CELL,
    PROFILES,
    RATING_SLOTS_PER_ROUND,
    ROUNDS_PER_ATOM,
    SLOTS_PER_ROUND,
)

ID_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{1,62}[A-Za-z0-9]")
"""Batch, bank and run-unit IDs: 3-64 ASCII letters, digits and inner hyphens."""
DEMO_PREFIX: Final = "DEMO-"


class Study(StrEnum):
    A = "A"
    B = "B"


class Method(StrEnum):
    """Proposal method of a slot. `B` is the Study B bank proposer (frozen A3 core)."""

    A1 = "A1"
    A2 = "A2"
    A3 = "A3"
    B = "B"


STUDY_A_METHODS: Final[tuple[Method, ...]] = (Method.A1, Method.A2, Method.A3)


class RunKind(StrEnum):
    """What a run directory holds. Only `demo` and `synthetic` runs may enter git."""

    DEMO = "demo"
    SYNTHETIC = "synthetic"
    PRACTICE = "practice"
    PILOT = "pilot"
    CONFIRMATORY = "confirmatory"


PUBLIC_RUN_KINDS: Final[frozenset[RunKind]] = frozenset({RunKind.DEMO, RunKind.SYNTHETIC})


class IdError(ValueError):
    """An identifier does not match its format."""


def check_id(value: str, what: str = "ID") -> str:
    """Return `value` if it is a 3-64 character ID (letters, digits, inner hyphens)."""
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise IdError(f"{what} {value!r} must be 3-64 ASCII letters, digits and inner hyphens")
    return value


def check_book(book_id: str) -> str:
    """Return `book_id` if it is an anonymous book ID under the store's rules
    (`av_sound.store.check_book_id`: no method token or method word)."""
    try:
        return check_book_id(book_id)
    except ValueError as err:
        raise IdError(str(err)) from err


def check_atom(atom_id: str) -> str:
    if atom_id not in ATOM_IDS:
        raise IdError(f"not an atom ID: {atom_id!r}")
    return atom_id


def _check_range(name: str, value: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise IdError(f"{name} must be an integer {low}..{high}, got {value!r}")
    return value


def slot_index(round_: int, slot: int) -> int:
    """Submission-slot number 1..12 within an atom (tie rule: lowest wins)."""
    _check_range("round", round_, 1, ROUNDS_PER_ATOM)
    _check_range("slot", slot, 1, SLOTS_PER_ROUND)
    return (round_ - 1) * SLOTS_PER_ROUND + slot


def round_and_slot(index: int) -> tuple[int, int]:
    """Inverse of `slot_index`."""
    _check_range("slot_index", index, 1, ROUNDS_PER_ATOM * SLOTS_PER_ROUND)
    return (index - 1) // SLOTS_PER_ROUND + 1, (index - 1) % SLOTS_PER_ROUND + 1


class ProposalSlot(NamedTuple):
    book_id: str
    atom_id: str
    round: int
    slot: int


class BankSlot(NamedTuple):
    bank_id: str
    attempt: int
    profile: str
    atom_id: str
    slot: int


class RatingSlot(NamedTuple):
    batch_id: str
    atom_id: str
    round: int
    position: int


def proposal_slot_id(book_id: str, atom_id: str, round_: int, slot: int) -> str:
    """Study A proposal-slot ID, e.g. `BK-C-7QX4MN.K-a1.r2s3`."""
    check_book(book_id)
    check_atom(atom_id)
    slot_index(round_, slot)
    return f"{book_id}.{atom_id}.r{round_}s{slot}"


def bank_slot_id(bank_id: str, attempt: int, profile: str, atom_id: str, slot: int) -> str:
    """Study B proposal-slot ID, e.g. `bank-C001.t1.P2.Q-r4.s07`."""
    check_id(bank_id, "bank ID")
    _check_range("attempt", attempt, 1, B_MAX_ATTEMPTS)
    if profile not in PROFILES:
        raise IdError(f"not a profile: {profile!r}")
    check_atom(atom_id)
    _check_range("slot", slot, 1, B_SLOTS_PER_CELL)
    return f"{bank_id}.t{attempt}.{profile}.{atom_id}.s{slot:02d}"


def rating_slot_id(batch_id: str, atom_id: str, round_: int, position: int) -> str:
    """Rating-slot ID, e.g. `A-P01.K-a1.r2p5` (position 1..9 in the round's play order)."""
    check_id(batch_id, "batch ID")
    check_atom(atom_id)
    _check_range("round", round_, 1, ROUNDS_PER_ATOM)
    _check_range("position", position, 1, RATING_SLOTS_PER_ROUND)
    return f"{batch_id}.{atom_id}.r{round_}p{position}"


_PROPOSAL_RE: Final = re.compile(r"(.+)\.([KQ]-[ar][1-4])\.r([1-9])s([1-9])")
_BANK_RE: Final = re.compile(r"(.+)\.t([1-9])\.(P[1-3])\.([KQ]-[ar][1-4])\.s([0-9]{2})")
_RATING_RE: Final = re.compile(r"(.+)\.([KQ]-[ar][1-4])\.r([1-9])p([1-9])")


def parse_proposal_slot_id(value: str) -> ProposalSlot:
    match = _PROPOSAL_RE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise IdError(f"not a proposal-slot ID: {value!r}")
    parsed = ProposalSlot(match[1], match[2], int(match[3]), int(match[4]))
    if proposal_slot_id(*parsed) != value:
        raise IdError(f"not a canonical proposal-slot ID: {value!r}")
    return parsed


def parse_bank_slot_id(value: str) -> BankSlot:
    match = _BANK_RE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise IdError(f"not a bank-slot ID: {value!r}")
    parsed = BankSlot(match[1], int(match[2]), match[3], match[4], int(match[5]))
    if bank_slot_id(*parsed) != value:
        raise IdError(f"not a canonical bank-slot ID: {value!r}")
    return parsed


def parse_rating_slot_id(value: str) -> RatingSlot:
    match = _RATING_RE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise IdError(f"not a rating-slot ID: {value!r}")
    parsed = RatingSlot(match[1], match[2], int(match[3]), int(match[4]))
    if rating_slot_id(*parsed) != value:
        raise IdError(f"not a canonical rating-slot ID: {value!r}")
    return parsed


def is_demo(identifier: str) -> bool:
    """True for synthetic DEMO identifiers (`DEMO-...`)."""
    return identifier.startswith(DEMO_PREFIX)
