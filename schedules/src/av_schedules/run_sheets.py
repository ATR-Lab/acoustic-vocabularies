"""Visit run sheets (#32): one CSV per person and visit, one row per schedule block.

The columns are exactly those of the methodology ``visit-run-sheet-template.csv``
(``planning.RUN_SHEET_COLUMNS``; Common procedures section 7, Study B protocol section
11). The generator pre-fills:

* ``participant_id``: the person slot ID (``A-C01-L03``, ``B-C01-M1``). Run sheets are
  made before people are known; the reveal API (#31) binds a slot to a coded
  participant ID at allocation and the console (#73) shows or substitutes that ID.
* ``visit``, ``block`` and ``expected_count``: from the person's visit schedule (#30),
  rows in block order.
* ``hash_check``: when a package-hash mapping is supplied, the expected hash of the
  package the person uses: ``sha256:<64 hex>`` (Study A: the book's package, found
  through the learner-facing slot list; Study B: the dyad's package). Placeholder
  mappings for DEMO examples give ``DEMO-placeholder:<64 hex>``, which never matches a
  real package. Without a mapping the cell is left empty.

Every other column (``actual_count``, ``start_time``, ``end_time``, ``comfort_check``,
``phone_locked``, ``deviations``, ``operator_signoff``) is left empty for the operator.
A run sheet carries no method label, role, intended answer or allocation factor.

Files are UTF-8 with ``\\n`` line endings (the template itself uses ``\\r\\n``).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from .assign import build_a_allocation
from .curriculum import dumps_json
from .design import SET_NAMES, SetName, Unit
from .findings import Finding
from .masking import find_method_strings
from .matrix import Study
from .orders import person_ids, visit_plan
from .planning import RUN_SHEET_COLUMNS
from .seeds import MasterSeed

PREFILLED_COLUMNS: Final[tuple[str, ...]] = (
    "participant_id",
    "visit",
    "block",
    "expected_count",
    "hash_check",
)
OPERATOR_COLUMNS: Final[tuple[str, ...]] = tuple(
    c for c in RUN_SHEET_COLUMNS if c not in PREFILLED_COLUMNS
)
HASH_PREFIX: Final = "sha256:"
PLACEHOLDER_PREFIX: Final = "DEMO-placeholder:"
PACKAGE_HASHES_FORMAT: Final = "av-schedules/package-hashes"
PACKAGE_HASHES_FORMAT_VERSION: Final = 1
_HEX64: Final = re.compile(r"^[0-9a-f]{64}$")
_PACKAGE_HASHES_KEYS: Final = frozenset(
    {"format", "format_version", "study", "set", "demo", "placeholder", "packages"}
)


# ---------------------------------------------------------------------------------------
# Package-hash mapping (input from the package builder, #13)


@dataclass(frozen=True)
class PackageHashes:
    """Expected package hashes of one study and set.

    ``packages`` maps a Study A book ID (``BK-C-7QX4MN``) or a Study B dyad slot ID
    (``B-C01``) to the package's SHA-256 (64 lowercase hex). ``placeholder`` marks DEMO
    placeholder values (never real packages). ``sha256`` is the SHA-256 of the mapping
    document's bytes.
    """

    study: Study
    set_name: SetName
    demo: bool
    placeholder: bool
    packages: tuple[tuple[str, str], ...]
    sha256: str

    def cell(self, key: str) -> str | None:
        """The ``hash_check`` cell for a package key, or None if the key is absent."""
        for k, digest in self.packages:
            if k == key:
                return (PLACEHOLDER_PREFIX if self.placeholder else HASH_PREFIX) + digest
        return None

    def keys(self) -> tuple[str, ...]:
        return tuple(k for k, _ in self.packages)


def package_hashes_document(
    study: Study,
    set_name: SetName,
    packages: Mapping[str, str],
    *,
    demo: bool,
    placeholder: bool = False,
) -> dict[str, Any]:
    """A ``package-hashes`` document (schema ``package-hashes.schema.json``)."""
    return {
        "format": PACKAGE_HASHES_FORMAT,
        "format_version": PACKAGE_HASHES_FORMAT_VERSION,
        "study": study,
        "set": set_name,
        "demo": demo,
        "placeholder": placeholder,
        "packages": dict(sorted(packages.items())),
    }


def parse_package_hashes(data: bytes) -> PackageHashes:
    """Read and validate a package-hash mapping; raises ``ValueError`` when malformed."""
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"package-hash mapping is not UTF-8 JSON: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("format") != PACKAGE_HASHES_FORMAT:
        raise ValueError(f"not a {PACKAGE_HASHES_FORMAT} document")
    if set(doc) != _PACKAGE_HASHES_KEYS:
        raise ValueError(
            f"package-hash mapping fields {sorted(doc)} != {sorted(_PACKAGE_HASHES_KEYS)}"
        )
    if doc["format_version"] != PACKAGE_HASHES_FORMAT_VERSION:
        raise ValueError(f"unsupported format_version {doc['format_version']!r}")
    study, set_name = doc["study"], doc["set"]
    if study not in ("A", "B") or set_name not in SET_NAMES:
        raise ValueError(f"bad study/set {study!r}/{set_name!r}")
    demo, placeholder = doc["demo"], doc["placeholder"]
    if not isinstance(demo, bool) or not isinstance(placeholder, bool):
        raise ValueError("demo and placeholder must be booleans")
    if placeholder and not demo:
        raise ValueError("placeholder hashes are allowed only in DEMO mappings")
    packages = doc["packages"]
    if not isinstance(packages, dict) or not packages:
        raise ValueError("packages must be a non-empty object")
    for key, digest in packages.items():
        if not isinstance(digest, str) or not _HEX64.match(digest):
            raise ValueError(f"package {key!r}: {digest!r} is not 64 lowercase hex digits")
    return PackageHashes(
        study=study,
        set_name=set_name,
        demo=demo,
        placeholder=placeholder,
        packages=tuple(sorted(packages.items())),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def load_package_hashes(path: Path) -> PackageHashes:
    """Read a package-hash mapping file (see :func:`parse_package_hashes`)."""
    return parse_package_hashes(path.read_bytes())


def package_keys(master: MasterSeed, units: Sequence[Unit]) -> dict[str, str]:
    """Package key of every person slot of ``units``.

    Study A: the book ID of the slot from the learner-facing slot list (#31; never the
    restricted key). Study B: the dyad slot ID (both members share the dyad package).
    """
    if not units:
        return {}
    first = units[0]
    if first.study == "B":
        return {p: u.unit_id for u in units for p in person_ids(u)}
    alloc = build_a_allocation(master, first.set_name)
    book_of = {s.slot_id: s.book_id for s in alloc.slots}
    return {p: book_of[p] for u in units for p in person_ids(u)}


def placeholder_package_hashes(master: MasterSeed, units: Sequence[Unit]) -> bytes:
    """DEMO placeholder mapping for ``units`` (DEMO seeds only), as file bytes.

    Each value is ``sha256("DEMO-placeholder|<seed label>|<study>|<set>|<key>")``.
    """
    if not master.demo:
        raise ValueError("placeholder package hashes are only for DEMO seeds")
    if not units:
        raise ValueError("no units")
    study, set_name = units[0].study, units[0].set_name
    keys = sorted(set(package_keys(master, units).values()))
    packages = {
        k: hashlib.sha256(
            f"DEMO-placeholder|{master.label}|{study}|{set_name}|{k}".encode()
        ).hexdigest()
        for k in keys
    }
    doc = package_hashes_document(study, set_name, packages, demo=True, placeholder=True)
    return dumps_json(doc)


def hash_cells(
    master: MasterSeed, units: Sequence[Unit], hashes: PackageHashes | None
) -> dict[str, str]:
    """The ``hash_check`` cell of every person slot ("" without a mapping).

    The mapping must belong to the same study and set, match the seed's DEMO status,
    cover every main unit's package and name no package outside the set. Spare dyad
    slots without a package get an empty cell.
    """
    persons = [p for u in units for p in person_ids(u)]
    if hashes is None:
        return dict.fromkeys(persons, "")
    if not units:
        return {}
    study, set_name = units[0].study, units[0].set_name
    if (hashes.study, hashes.set_name) != (study, set_name):
        raise ValueError(
            f"package-hash mapping is for {hashes.study} {hashes.set_name}, not {study} {set_name}"
        )
    if hashes.demo != master.demo:
        raise ValueError("package-hash mapping and master seed differ in DEMO status")
    keys = package_keys(master, units)
    spare_keys = {keys[p] for u in units if u.kind == "spare" for p in person_ids(u)}
    unknown = sorted(set(hashes.keys()) - set(keys.values()))
    if unknown:
        raise ValueError(f"package-hash mapping names packages outside the set: {unknown[:5]}")
    out: dict[str, str] = {}
    missing: set[str] = set()
    for person in persons:
        cell = hashes.cell(keys[person])
        if cell is None and keys[person] not in spare_keys:
            missing.add(keys[person])
        out[person] = cell or ""
    if missing:
        raise ValueError(f"package-hash mapping lacks packages: {sorted(missing)[:5]}")
    return out


# ---------------------------------------------------------------------------------------
# Run sheets


def run_sheet_path(unit_id: str, person_id: str, visit: str) -> str:
    """Path of one run sheet relative to ``<out>/<study>/``."""
    return f"{unit_id}/run-sheets/{person_id}/{visit}.csv"


def run_sheet_rows(doc: Mapping[str, Any], hash_cell: str = "") -> list[dict[str, str]]:
    """Rows of the run sheet of one visit schedule (one per block, in block order)."""
    rows = []
    for b in doc["blocks"]:
        row = dict.fromkeys(RUN_SHEET_COLUMNS, "")
        row.update(
            participant_id=str(doc["person_id"]),
            visit=str(doc["visit"]),
            block=str(b["block"]),
            expected_count=str(b["expected_count"]),
            hash_check=hash_cell,
        )
        rows.append(row)
    return rows


def run_sheet_csv(doc: Mapping[str, Any], hash_cell: str = "") -> bytes:
    """The run-sheet CSV of one visit schedule (template header, LF, UTF-8)."""
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=RUN_SHEET_COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(run_sheet_rows(doc, hash_cell))
    return buf.getvalue().encode("utf-8")


def read_run_sheet(data: bytes) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    """Header and rows of a run-sheet CSV (a UTF-8 byte-order mark is tolerated)."""
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
    rows = [dict(r) for r in reader]
    return tuple(reader.fieldnames or ()), rows


def run_sheet_findings(data: bytes, doc: Mapping[str, Any], hash_cell: str = "") -> list[Finding]:
    """Check one run sheet against its visit schedule and the visit plan.

    Rule ``run-sheet``: header equal to the template; one row per block in plan order;
    ``participant_id``, ``visit``, ``block`` and ``expected_count`` match the schedule
    and the plan; ``hash_check`` equals the expected cell; operator columns empty.
    Rule ``masking`` (Study A): no method string anywhere in the file.
    """
    unit, person, visit = str(doc["unit_id"]), str(doc["person_id"]), str(doc["visit"])
    out: list[Finding] = []

    def bad(detail: str, rule: str = "run-sheet") -> None:
        out.append(Finding(unit, person, visit, rule, detail))

    try:
        header, rows = read_run_sheet(data)
    except (UnicodeDecodeError, csv.Error) as exc:
        bad(f"unreadable: {exc}")
        return out
    if header != RUN_SHEET_COLUMNS:
        bad(f"header {list(header)} != template {list(RUN_SHEET_COLUMNS)}")
        return out
    plan = visit_plan(doc["study"], visit)
    blocks = list(doc["blocks"])
    if len(rows) != len(plan) or len(rows) != len(blocks):
        bad(f"{len(rows)} rows, expected {len(plan)} blocks")
    for n, (row, p, b) in enumerate(zip(rows, plan, blocks, strict=False), start=1):
        want = {
            "participant_id": person,
            "visit": visit,
            "block": p.block,
            "expected_count": str(p.count),
            "hash_check": hash_cell,
        }
        for column, value in want.items():
            if row.get(column) != value:
                bad(f"row {n} {column}={row.get(column)!r}, expected {value!r}")
        if row.get("block") != b["block"] or row.get("expected_count") != str(len(b["items"])):
            bad(f"row {n} differs from schedule block {b['block']} ({len(b['items'])} items)")
        filled = [c for c in OPERATOR_COLUMNS if row.get(c)]
        if filled:
            bad(f"row {n} operator columns not empty: {filled}")
    if doc["study"] == "A":
        found = find_method_strings(data.decode("utf-8", errors="replace"))
        if found:
            bad(f"method strings {sorted(set(found))}", rule="masking")
    return out
