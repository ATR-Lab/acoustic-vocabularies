"""The unit permutation a bank is built for (file contract with the schedules stack, #29/#31).

The bank builder binds a bank to its dyad slot through the unit's package-safe
`<unit>/permutation.json` (format `av-schedules/permutation` v2, written by
`av_schedules`): the semantic label of every atom and the stored atom order, which is
also the bank traversal order (Study B protocol §4). It never reads the allocation lists
(`<set>-dyads.json`): roles, arms, swaps and menu orders stay concealed from generation.

Bank IDs are placeholders by dyad-slot sequence (schedules allocation, #31): pilot
`bank-P<nnn>` <-> unit `B-P<nn>`; confirmatory `bank-C001`..`bank-C064` <-> `B-C01`..`B-C64`
(main slots) and `bank-C065`..`bank-C072` <-> `B-S01`..`B-S08` (spares). The mapping uses
no random draw and carries no allocation information. DEMO banks take any DEMO unit.

This module checks only the fields the builder uses (it does not import the schedules
package: cross-stack inputs are file contracts). `permutation_sha256` is the SHA-256 of
the file bytes; the builder stores a byte copy beside the bank manifest.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from av_generation.ids import CONFIRMATORY_BANK_RE, PILOT_BANK_RE, bank_set
from av_sound.grammar import ATOM_IDS, parse_atom_id
from av_sound.recipe import StrictJsonError, strict_json_loads
from av_sound.store import SEMANTIC_LABELS

PERMUTATION_FORMAT: Final = "av-schedules/permutation"
PERMUTATION_VERSION: Final = 2
B_UNIT_RE: Final = re.compile(r"B-[PCS][0-9]{2}")
CONFIRMATORY_MAIN_SLOTS: Final = 64
"""Main confirmatory dyad slots (Study B: 64 dyads); spares continue the numbering."""


class PermutationError(ValueError):
    """The permutation file is unusable for a bank, or belongs to another dyad slot."""


@dataclass(frozen=True, slots=True)
class UnitPermutation:
    """What the bank builder reads from a unit's `permutation.json`."""

    unit_id: str
    set: str
    """`pilot` or `confirmatory` (the schedules set; DEMO units carry one too)."""
    demo: bool
    labels: Mapping[str, str]
    """Atom ID -> semantic label."""
    atom_order: tuple[str, ...]
    """The stored order: wave 1, 2, 3 menu orders concatenated (bank traversal order)."""
    sha256: str
    """SHA-256 of the file bytes (`permutation_sha256` of the bank manifest)."""
    data: bytes
    """The file bytes (copied beside the manifest)."""


def expected_unit_id(bank_id: str) -> str | None:
    """The dyad slot (unit ID) of a pilot or confirmatory bank ID; `None` for DEMO banks."""
    if PILOT_BANK_RE.fullmatch(bank_id):
        return f"B-P{int(bank_id[6:]):02d}"
    if CONFIRMATORY_BANK_RE.fullmatch(bank_id):
        seq = int(bank_id[6:])
        if 1 <= seq <= CONFIRMATORY_MAIN_SLOTS:
            return f"B-C{seq:02d}"
        if seq > CONFIRMATORY_MAIN_SLOTS:
            return f"B-S{seq - CONFIRMATORY_MAIN_SLOTS:02d}"
        raise PermutationError(f"{bank_id}: bank sequence numbers start at 001")
    bank_set(bank_id)  # raises IdError for anything that is not a bank ID
    return None


def _labels(doc: Mapping[str, Any]) -> dict[str, str]:
    atoms = doc.get("atoms")
    if not isinstance(atoms, list) or len(atoms) != len(ATOM_IDS):
        raise PermutationError("permutation: 'atoms' must list the 16 atoms")
    labels: dict[str, str] = {}
    for entry in atoms:
        if not isinstance(entry, Mapping):
            raise PermutationError("permutation: every atom entry is an object")
        atom = entry.get("atom_id")
        label = entry.get("semantic_label")
        if atom not in ATOM_IDS or atom in labels or not isinstance(label, str):
            raise PermutationError(f"permutation: bad or repeated atom entry {atom!r}")
        labels[str(atom)] = label
    for (family, role), allowed in SEMANTIC_LABELS.items():
        bound = sorted(
            labels[a]
            for a in ATOM_IDS
            if parse_atom_id(a).family == family and parse_atom_id(a).role == role
        )
        if bound != sorted(allowed):
            raise PermutationError(
                f"permutation: {family} {role} labels {bound} are not a permutation of "
                f"{list(allowed)}"
            )
    return labels


def parse_permutation(data: bytes) -> UnitPermutation:
    """Check a Study B `permutation.json` and return the builder's view of it."""
    try:
        doc = strict_json_loads(data)
    except StrictJsonError as err:
        raise PermutationError(f"permutation: {err}") from err
    if not isinstance(doc, dict):
        raise PermutationError("permutation: expected a JSON object")
    if doc.get("format") != PERMUTATION_FORMAT or doc.get("format_version") != PERMUTATION_VERSION:
        raise PermutationError(
            f"permutation: expected format {PERMUTATION_FORMAT!r} version {PERMUTATION_VERSION}"
        )
    if doc.get("study") != "B":
        raise PermutationError("permutation: not a Study B unit")
    unit_id = doc.get("unit_id")
    if not isinstance(unit_id, str) or not B_UNIT_RE.fullmatch(unit_id):
        raise PermutationError(f"permutation: bad unit_id {unit_id!r}")
    set_name = doc.get("set")
    if set_name not in ("pilot", "confirmatory"):
        raise PermutationError(f"permutation: bad set {set_name!r}")
    demo = doc.get("demo")
    if not isinstance(demo, bool):
        raise PermutationError("permutation: 'demo' must be a boolean")
    labels = _labels(doc)
    order = doc.get("atom_order")
    if not isinstance(order, list) or sorted(map(str, order)) != sorted(ATOM_IDS):
        raise PermutationError("permutation: 'atom_order' must order the 16 atoms")
    return UnitPermutation(
        unit_id=unit_id,
        set=str(set_name),
        demo=demo,
        labels=labels,
        atom_order=tuple(str(a) for a in order),
        sha256=hashlib.sha256(data).hexdigest(),
        data=bytes(data),
    )


def load_permutation(path: str | os.PathLike[str]) -> UnitPermutation:
    """Read `path` (a `permutation.json` file or the unit directory holding it)."""
    source = Path(path)
    if source.is_dir():
        source = source / "permutation.json"
    return parse_permutation(source.read_bytes())


def check_bank_unit(bank_id: str, permutation: UnitPermutation) -> None:
    """Refuse a permutation that does not belong to `bank_id`.

    DEMO banks need a DEMO unit; pilot and confirmatory banks need a non-DEMO unit of the
    same set whose unit ID matches the bank's dyad-slot sequence (`expected_unit_id`).
    """
    found_set = bank_set(bank_id)
    if found_set == "demo":
        if not permutation.demo:
            raise PermutationError(f"{bank_id}: DEMO banks are built from DEMO units only")
        return
    if permutation.demo:
        raise PermutationError(f"{bank_id}: a {found_set} bank cannot use a DEMO unit")
    if permutation.set != found_set:
        raise PermutationError(f"{bank_id}: a {found_set} bank cannot use a {permutation.set} unit")
    expected = expected_unit_id(bank_id)
    if permutation.unit_id != expected:
        raise PermutationError(
            f"{bank_id} belongs to dyad slot {expected}, not {permutation.unit_id} "
            "(dyad-slot sequence, schedules allocation #31)"
        )
