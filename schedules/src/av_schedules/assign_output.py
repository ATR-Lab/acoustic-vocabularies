"""Allocation list documents, balance report and manifest for one study and set.

Layout under ``<out>/<study>/`` (restricted storage; never committed except DEMO)::

    <set>-slots.json            Study A learner-facing list: slot -> anonymous book ID
    <set>-book-key.json         Study A restricted key: book ID -> batch, method, designer
    <set>-dyads.json            Study B concealed list: dyad slot -> roles, bank, menu order
    <set>-assign-balance.csv    balance report (coordinator material)
    <set>-assign-manifest.json  seed fingerprint, generator, list and file SHA-256

Every list document stores ``generator``, ``seed_label``, ``allocation_seed`` (SHA-256
fingerprint of the master seed, never the seed) and ``list_sha256``: the SHA-256 of the
document's canonical JSON without that field (``sort_keys``, separators ``,`` and ``:``,
UTF-8). Files are written with ``indent=2``, sorted keys and a trailing newline.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections.abc import Callable, Mapping, Sequence
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

from .assign import (
    A_LEARNERS_PER_BOOK,
    A_METHODS,
    AAllocation,
    BAllocation,
    build_a_allocation,
    build_b_allocation,
    generator_info,
)
from .balance import BalanceRow, balance_csv, max_abs_deviation
from .curriculum import dumps_json
from .design import B_CELLS, B_DEFAULT_SPARES, DESIGNERS, PROFILES, SET_NAMES, SetName
from .masking import assert_masked
from .matrix import Study
from .seeds import MasterSeed, demo_seed

FORMAT_VERSION: Final = 1
A_SLOTS_FORMAT: Final = "av-schedules/a-slots"
A_KEY_FORMAT: Final = "av-schedules/a-book-key"
B_DYADS_FORMAT: Final = "av-schedules/b-dyads"
MANIFEST_FORMAT: Final = "av-schedules/assign-manifest"
LIST_FORMATS: Final[tuple[str, ...]] = (A_SLOTS_FORMAT, A_KEY_FORMAT, B_DYADS_FORMAT)
AUDIENCE: Final[dict[str, str]] = {
    A_SLOTS_FORMAT: "learner-facing",
    A_KEY_FORMAT: "restricted",
    B_DYADS_FORMAT: "concealed",
}

# Public DEMO examples committed under schedules/examples/demo-allocation/. The
# confirmatory seed is the curriculum example seed, so its design tables match
# schedules/examples/demo/; the pilot uses its own seed (pilot and confirmatory lists
# never share a master seed).
EXAMPLE_SEEDS: Final[dict[SetName, str]] = {
    "pilot": "DEMO-o4.4.3-pilot",
    "confirmatory": "DEMO-o4.4.1-example",
}


def canonical_sha256(doc: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON of ``doc`` without its ``list_sha256`` field."""
    body = {k: v for k, v in doc.items() if k != "list_sha256"}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _seal(doc: dict[str, Any]) -> dict[str, Any]:
    doc["list_sha256"] = canonical_sha256(doc)
    return doc


def _header(alloc: AAllocation | BAllocation, study: Study, fmt: str) -> dict[str, Any]:
    return {
        "format": fmt,
        "format_version": FORMAT_VERSION,
        "audience": AUDIENCE[fmt],
        "generator": generator_info(),
        "demo": alloc.demo,
        "seed_label": alloc.seed_label,
        "allocation_seed": alloc.allocation_seed,
        "study": study,
        "set": alloc.set_name,
        "design_table_sha256": alloc.design_table_sha256,
    }


def a_slots_document(alloc: AAllocation) -> dict[str, Any]:
    """Learner-facing list: slots in reveal order with anonymous book IDs only."""
    doc = _header(alloc, "A", A_SLOTS_FORMAT)
    doc["counts"] = {
        "batches": len(alloc.batches),
        "waves": len(alloc.waves),
        "slots": len(alloc.slots),
        "slots_per_batch": A_LEARNERS_PER_BOOK[alloc.set_name] * len(A_METHODS),
    }
    doc["waves"] = [{"wave": w, "units": list(u)} for w, u in enumerate(alloc.waves, start=1)]
    doc["slots"] = [
        {
            "slot_id": s.slot_id,
            "unit_id": s.unit_id,
            "slot": s.slot,
            "order": s.order,
            "wave": s.wave,
            "wave_position": s.wave_position,
            "profile": s.profile,
            "book_id": s.book_id,
        }
        for s in alloc.slots
    ]
    return _seal(doc)


def a_book_key_document(alloc: AAllocation, slots_list_sha256: str) -> dict[str, Any]:
    """Restricted key: book ID -> batch, method and A1 designer (study coordinator)."""
    doc = _header(alloc, "A", A_KEY_FORMAT)
    doc["slots_list_sha256"] = slots_list_sha256
    doc["books"] = [
        {
            "book_id": b.book_id,
            "unit_id": b.unit_id,
            "method": b.method,
            "designer": b.designer,
            "slots": list(b.slots),
        }
        for b in alloc.books
    ]
    return _seal(doc)


def b_dyads_document(alloc: BAllocation) -> dict[str, Any]:
    """Concealed Study B list: every dyad slot with roles, bank and menu order."""
    doc = _header(alloc, "B", B_DYADS_FORMAT)
    doc["counts"] = {
        "dyads": sum(1 for d in alloc.dyads if d.kind == "dyad"),
        "spares": sum(1 for d in alloc.dyads if d.kind == "spare"),
    }
    doc["dyads"] = [
        {
            "unit_id": d.unit_id,
            "kind": d.kind,
            "order": d.order,
            "block_id": d.block_id,
            "block_position": d.block_position,
            "sq_arm": d.sq_arm,
            "structured_family": d.structured_family,
            "swap_w1_w4": d.swap_w1_w4,
            "members": [
                {"slot_id": m.slot_id, "member": m.member, "role": m.role} for m in d.members
            ],
            "bank_id": d.bank_id,
            "profile_menu_order": list(d.profile_menu_order),
        }
        for d in alloc.dyads
    ]
    return _seal(doc)


# ---------------------------------------------------------------------------------------
# Balance report (same columns as the curriculum balance report)


_MakeRow = Callable[[str, str, int, Fraction], BalanceRow]


def _row(alloc: AAllocation | BAllocation, study: str, metric: str, scope: str) -> _MakeRow:
    def make(item: str, position: str, count: int, expected: Fraction) -> BalanceRow:
        return BalanceRow(
            study, alloc.set_name, metric, scope, "", "", item, position, count, expected
        )

    return make


def a_balance_rows(alloc: AAllocation) -> tuple[BalanceRow, ...]:
    """Waves (profile, designer, swap, position) and per-batch method counts."""
    rows: list[BalanceRow] = []
    batch = {b.unit_id: b for b in alloc.batches}
    for w, wave in enumerate(alloc.waves, start=1):
        scope = f"wave={w}"
        factors: tuple[tuple[str, tuple[str, ...], list[str]], ...] = (
            ("wave_profile", PROFILES, [batch[u].profile for u in wave]),
            ("wave_designer", DESIGNERS, [batch[u].designer for u in wave]),
            ("wave_swap_w1_w4", ("0", "1"), [str(int(batch[u].swap_w1_w4)) for u in wave]),
        )
        for metric, levels, values in factors:
            make = _row(alloc, "A", metric, scope)
            for level in levels:
                rows.append(make(level, "", values.count(level), Fraction(len(wave), len(levels))))
    make = _row(alloc, "A", "wave_position_profile", "all")
    for p in PROFILES:
        for pos in range(1, 4):
            n = sum(1 for wave in alloc.waves if batch[wave[pos - 1]].profile == p)
            rows.append(make(p, str(pos), n, Fraction(len(alloc.waves), 3)))
    per_book = A_LEARNERS_PER_BOOK[alloc.set_name]
    for b in alloc.batches:
        make = _row(alloc, "A", "batch_method", f"unit={b.unit_id}")
        for book in (k for k in alloc.books if k.unit_id == b.unit_id):
            rows.append(make(book.method, "", len(book.slots), Fraction(per_book)))
    # Slot positions are an unconstrained random permutation (protocol section 4).
    make = _row(alloc, "A", "slot_method", "all")
    n_slots = per_book * len(A_METHODS)
    for m in A_METHODS:
        for i in range(1, n_slots + 1):
            n = sum(
                1
                for b in alloc.books
                if b.method == m and any(s.endswith(f"-L{i:02d}") for s in b.slots)
            )
            rows.append(make(m, f"L{i:02d}", n, Fraction(len(alloc.batches), len(A_METHODS))))
    return tuple(rows)


def b_balance_rows(alloc: BAllocation) -> tuple[BalanceRow, ...]:
    """SQ arm, swap within arm, member-1 role per cell and profile-menu orders."""
    rows: list[BalanceRow] = []
    main = [d for d in alloc.dyads if d.kind == "dyad"]
    scopes = [("all", main)]
    if len(main) != len(alloc.dyads):
        scopes.append(("all+spares", list(alloc.dyads)))
        scopes.append(("spares", [d for d in alloc.dyads if d.kind == "spare"]))
    for scope, members in scopes:
        n = len(members)
        make = _row(alloc, "B", "sq_arm", scope)
        for arm in ("SQ-1", "SQ-2"):
            rows.append(make(arm, "", sum(1 for d in members if d.sq_arm == arm), Fraction(n, 2)))
        for arm, swap in B_CELLS:
            cell = [d for d in members if d.sq_arm == arm and d.swap_w1_w4 == swap]
            make = _row(alloc, "B", "member1_role", f"{scope}:{arm}:swap_w1_w4={int(swap)}")
            m1 = sum(1 for d in cell if d.active_member == 1)
            rows.append(make("active", "", m1, Fraction(len(cell), 2)))
            rows.append(make("yoked", "", len(cell) - m1, Fraction(len(cell), 2)))
        for arm in ("SQ-1", "SQ-2"):
            in_arm = [d for d in members if d.sq_arm == arm]
            make = _row(alloc, "B", "swap_w1_w4", f"{scope}:{arm}")
            for swap in (False, True):
                k = sum(1 for d in in_arm if d.swap_w1_w4 == swap)
                rows.append(make(str(int(swap)), "", k, Fraction(len(in_arm), 2)))
        make = _row(alloc, "B", "menu_order", scope)
        for order in itertools.permutations(PROFILES):
            k = sum(1 for d in members if d.profile_menu_order == order)
            rows.append(make("|".join(order), "", k, Fraction(n, 6)))
        make = _row(alloc, "B", "menu_position", scope)
        for p in PROFILES:
            for pos in range(1, 4):
                k = sum(1 for d in members if d.profile_menu_order[pos - 1] == p)
                rows.append(make(p, str(pos), k, Fraction(n, 3)))
    return tuple(rows)


# ---------------------------------------------------------------------------------------
# Files


def a_files(alloc: AAllocation) -> dict[str, bytes]:
    """Study A files of one set (paths relative to ``<out>/A/``), manifest last."""
    set_name = alloc.set_name
    slots = a_slots_document(alloc)
    slots_bytes = dumps_json(slots)
    assert_masked(slots_bytes.decode("utf-8"), f"{set_name}-slots.json")
    key = a_book_key_document(alloc, slots["list_sha256"])
    files = {
        f"{set_name}-slots.json": slots_bytes,
        f"{set_name}-book-key.json": dumps_json(key),
    }
    rows = a_balance_rows(alloc)
    files[f"{set_name}-assign-balance.csv"] = balance_csv(rows)
    lists = {f"{set_name}-slots.json": slots["list_sha256"]}
    lists[f"{set_name}-book-key.json"] = key["list_sha256"]
    files[f"{set_name}-assign-manifest.json"] = _manifest(alloc, "A", files, lists, rows)
    return files


def b_files(alloc: BAllocation) -> dict[str, bytes]:
    """Study B files of one set (paths relative to ``<out>/B/``), manifest last."""
    set_name = alloc.set_name
    dyads = b_dyads_document(alloc)
    files = {f"{set_name}-dyads.json": dumps_json(dyads)}
    rows = b_balance_rows(alloc)
    files[f"{set_name}-assign-balance.csv"] = balance_csv(rows)
    lists = {f"{set_name}-dyads.json": dyads["list_sha256"]}
    files[f"{set_name}-assign-manifest.json"] = _manifest(alloc, "B", files, lists, rows)
    return files


def _manifest(
    alloc: AAllocation | BAllocation,
    study: Study,
    files: Mapping[str, bytes],
    lists: Mapping[str, str],
    rows: Sequence[BalanceRow],
) -> bytes:
    doc = {
        "format": MANIFEST_FORMAT,
        "format_version": FORMAT_VERSION,
        "generator": generator_info(),
        "demo": alloc.demo,
        "seed_label": alloc.seed_label,
        "allocation_seed": alloc.allocation_seed,
        "study": study,
        "set": alloc.set_name,
        "design_table_sha256": alloc.design_table_sha256,
        "lists": dict(sorted(lists.items())),
        "files": {p: hashlib.sha256(d).hexdigest() for p, d in sorted(files.items())},
        "balance_max_abs_deviation": max_abs_deviation(rows),
    }
    return dumps_json(doc)


def assign_files(
    master: MasterSeed, study: Study, set_name: SetName, *, spares: int = B_DEFAULT_SPARES
) -> dict[str, bytes]:
    """Build the allocation of one study and set and render all of its files."""
    if study == "A":
        return a_files(build_a_allocation(master, set_name))
    return b_files(build_b_allocation(master, set_name, spares=spares))


def manifest_name(set_name: SetName) -> str:
    return f"{set_name}-assign-manifest.json"


def existing_allocation_seed_label(study_dir: Path, set_name: SetName) -> str | None:
    """``seed_label`` of allocation lists already written to ``study_dir``, if any."""
    path = study_dir / manifest_name(set_name)
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8")).get("seed_label")
    return value if isinstance(value, str) else None


def load_list(path: Path) -> dict[str, Any]:
    """Read a list document and verify its format and ``list_sha256``.

    Raises ``ValueError`` for an unknown format or a hash mismatch (edited file).
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("format") not in LIST_FORMATS:
        raise ValueError(f"{path.name} is not an allocation list document")
    if doc.get("list_sha256") != canonical_sha256(doc):
        raise ValueError(f"{path.name}: list_sha256 does not match the content")
    return doc


def demo_allocation_files() -> dict[str, bytes]:
    """The committed DEMO examples, keyed by path relative to ``examples/demo-allocation``."""
    out: dict[str, bytes] = {}
    studies: tuple[Study, ...] = ("A", "B")
    for study in studies:
        for set_name in SET_NAMES:
            master = demo_seed(EXAMPLE_SEEDS[set_name])
            for rel, data in assign_files(master, study, set_name).items():
                out[f"{study}/{rel}"] = data
    return out
