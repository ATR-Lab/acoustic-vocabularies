"""Per-unit curriculum tables (``curriculum.csv``) and ``permutation.json`` documents."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from typing import Any, Final

from . import __version__
from .design import ABatch, BDyadSlot, Unit
from .matrix import (
    A_VISITS,
    B_VISITS,
    FAMILIES,
    ROLES,
    atom_wave,
    atoms,
    cells,
    novel_visit,
    parse_atom_id,
)

# Columns of planning-materials/curriculum.csv, in order.
ORIGINAL_COLUMNS: Final[tuple[str, ...]] = (
    "family",
    "action_index",
    "referent_index",
    "message_id",
    "components_available_wave",
    "training_wave",
    "B_first_novel_visit_default",
    "A_novel_default",
    "counterbalance",
)
# Columns added per unit. ``novel_visit`` is the realised test visit after the swap.
ADDED_COLUMNS: Final[tuple[str, ...]] = (
    "unit_id",
    "semantic_action",
    "semantic_referent",
    "heldout_set",
    "swap_w1_w4",
    "seed",
    "novel_visit",
)
CURRICULUM_COLUMNS: Final[tuple[str, ...]] = ORIGINAL_COLUMNS + ADDED_COLUMNS

PERMUTATION_FORMAT: Final = "av-schedules/permutation"
PERMUTATION_FORMAT_VERSION: Final = 1


def _opt(value: object) -> str:
    return "" if value is None else str(value)


def curriculum_rows(unit: Unit) -> tuple[dict[str, str], ...]:
    """The unit's 32 curriculum rows (planning order), all values as strings."""
    rows = []
    for c in cells():
        hs = c.heldout_set
        rows.append(
            {
                "family": c.family,
                "action_index": str(c.action_index),
                "referent_index": str(c.referent_index),
                "message_id": c.message_id,
                "components_available_wave": str(c.components_available_wave),
                "training_wave": _opt(c.training_wave),
                "B_first_novel_visit_default": _opt(c.b_first_novel_visit_default),
                "A_novel_default": _opt(c.a_novel_default),
                "counterbalance": unit.block_id,
                "unit_id": unit.unit_id,
                "semantic_action": unit.permutation.label(c.family, "action", c.action_index),
                "semantic_referent": unit.permutation.label(c.family, "referent", c.referent_index),
                "heldout_set": _opt(hs),
                "swap_w1_w4": "1" if unit.swap_w1_w4 else "0",
                "seed": unit.seed,
                "novel_visit": "" if hs is None else novel_visit(unit.study, hs, unit.swap_w1_w4),
            }
        )
    return tuple(rows)


def curriculum_csv(unit: Unit) -> bytes:
    """``curriculum.csv`` bytes: UTF-8, LF line endings, header ``CURRICULUM_COLUMNS``."""
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=CURRICULUM_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(curriculum_rows(unit))
    return buf.getvalue().encode("utf-8")


def novel_by_visit(unit: Unit) -> dict[str, list[str]]:
    """Held-out message IDs tested at each visit (A: D0/D7; B: V1..W4), planning order."""
    visits = A_VISITS if unit.study == "A" else B_VISITS
    out: dict[str, list[str]] = {v: [] for v in visits}
    for c in cells():
        if c.heldout_set is None:
            continue
        visit = novel_visit(unit.study, c.heldout_set, unit.swap_w1_w4)
        if visit in out:
            out[visit].append(c.message_id)
    return out


def permutation_document(unit: Unit) -> dict[str, Any]:
    """The ``permutation.json`` document (schema: ``schema/permutation.schema.json``)."""
    position = {a: i + 1 for i, a in enumerate(unit.atom_order)}
    atom_entries = []
    for a in atoms():
        family, role, index = parse_atom_id(a)
        atom_entries.append(
            {
                "atom_id": a,
                "family": family,
                "role": role,
                "index": index,
                "semantic_label": unit.permutation.label(family, role, index),
                "matrix_wave": atom_wave(a),
                "order_position": position[a],
            }
        )
    messages = []
    for c in cells():
        hs = c.heldout_set
        messages.append(
            {
                "message_id": c.message_id,
                "family": c.family,
                "action_atom": c.action_atom,
                "referent_atom": c.referent_atom,
                "semantic_action": unit.permutation.label(c.family, "action", c.action_index),
                "semantic_referent": unit.permutation.label(c.family, "referent", c.referent_index),
                "status": "trained" if hs is None else "heldout",
                "training_wave": c.training_wave,
                "heldout_set": hs,
                "novel_visit": None if hs is None else novel_visit(unit.study, hs, unit.swap_w1_w4),
            }
        )
    doc: dict[str, Any] = {
        "format": PERMUTATION_FORMAT,
        "format_version": PERMUTATION_FORMAT_VERSION,
        "generator": {"name": "av-schedules", "version": __version__},
        "demo": unit.demo,
        "seed_label": unit.seed_label,
        "study": unit.study,
        "set": unit.set_name,
        "unit_id": unit.unit_id,
        "unit_kind": unit.kind,
        "shared_by": "batch" if unit.study == "A" else "dyad",
        "seed": unit.seed,
        "design": {
            "sequence": unit.sequence,
            "block_id": unit.block_id,
            "block_position": unit.block_position,
            "cycle": unit.cycle,
            "grid_row": unit.grid_row,
            "grid_col": unit.grid_col,
        },
        "swap_w1_w4": unit.swap_w1_w4,
        "family_first": unit.family_first,
        "labels": {f: {r: list(unit.permutation.labels(f, r)) for r in ROLES} for f in FAMILIES},
        "atoms": atom_entries,
        "atom_order": list(unit.atom_order),
        "messages": messages,
        "novel_by_visit": novel_by_visit(unit),
        "curriculum_csv_sha256": hashlib.sha256(curriculum_csv(unit)).hexdigest(),
    }
    if isinstance(unit, ABatch):
        # The A1 designer ID stays in the batch table: packages need no generation metadata.
        doc["study_a"] = {"profile": unit.profile}
    if isinstance(unit, BDyadSlot):
        doc["study_b"] = {
            "sq_arm": unit.sq_arm,
            "structured_family": unit.structured_family,
            "dictionary_family": unit.dictionary_family,
            "wave_atom_order": {
                str(w): list(order) for w, order in enumerate(unit.wave_orders, start=1)
            },
        }
    return doc


def dumps_json(doc: object) -> bytes:
    """Canonical JSON bytes: indent 2, sorted keys, UTF-8, trailing newline."""
    return (json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def permutation_json(unit: Unit) -> bytes:
    return dumps_json(permutation_document(unit))
