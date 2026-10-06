"""Unmasking: condition labels joined at analysis time only (#34). Interface; #34 implements.

The only module that reads ``keys/`` (``paths.INPUT_PATHS["book_key"]``: Study A book ID ->
method A1/A2/A3 and designer D1-D3, ``av-schedules/a-book-key``). Study B roles, the
scaffold allocation (``structured_family``) and the W1/W4 swap come from the dyad list
(``inputs/schedules/B/<set>-dyads.json``, ``av-schedules/b-dyads``); profile family per
Study A batch from the slot list. Lists are verified with their ``list_sha256`` (as
``av_schedules.assign_output.load_list``). Nothing from this module is ever written to
``reconciled/`` or ``monitoring/``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .paths import DataRoot


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


def load_conditions(root: DataRoot, study: str, set_name: str) -> Conditions:
    """Conditions of one study and set (slot or dyad list plus, for A, the book key)."""
    raise NotImplementedError("#34: unmasking")


def load_a_conditions(root: DataRoot, set_name: str) -> Mapping[str, BookCondition]:
    """Book ID -> condition."""
    raise NotImplementedError("#34: Study A unmasking")


def load_b_conditions(root: DataRoot, set_name: str) -> Mapping[str, DyadCondition]:
    """Dyad slot -> condition."""
    raise NotImplementedError("#34: Study B unmasking")
