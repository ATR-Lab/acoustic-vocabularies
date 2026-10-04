"""Render and write one study/set: unit folders, design table, balance report, manifest.

Layout under ``<out>/<study>/``::

    <unit_id>/curriculum.csv
    <unit_id>/permutation.json
    <set>-batch-table.csv     (Study A)  or  <set>-design-table.csv  (Study B)
    <set>-balance.csv
    <set>-manifest.json       (SHA-256 of every other file of the set)
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Final

from . import __version__
from .balance import balance_csv, balance_rows, max_abs_deviation
from .curriculum import curriculum_csv, dumps_json, permutation_json
from .design import SET_NAMES, ABatch, BDyadSlot, SetName, Unit, build_units
from .matrix import FAMILIES, ROLES, Study
from .seeds import MasterSeed, demo_seed

A_TABLE_COLUMNS: Final[tuple[str, ...]] = (
    "unit_id",
    "set",
    "sequence",
    "block_id",
    "block_position",
    "profile",
    "designer",
    "swap_w1_w4",
    "family_first",
    "K_action",
    "K_referent",
    "Q_action",
    "Q_referent",
    "atom_order",
    "seed",
    "seed_label",
)
B_TABLE_COLUMNS: Final[tuple[str, ...]] = (
    "unit_id",
    "set",
    "kind",
    "sequence",
    "block_id",
    "block_position",
    "sq_arm",
    "structured_family",
    "swap_w1_w4",
    "family_first",
    "K_action",
    "K_referent",
    "Q_action",
    "Q_referent",
    "V1_atom_order",
    "V2_atom_order",
    "V3_atom_order",
    "seed",
    "seed_label",
)
MANIFEST_FORMAT: Final = "av-schedules/manifest"

# Public demonstration outputs committed under schedules/examples/demo/.
EXAMPLE_DEMO_SEED: Final = "DEMO-o4.4.1-example"
EXAMPLE_UNITS: Final[tuple[str, ...]] = ("A-C01", "B-C01")


def table_name(study: Study, set_name: SetName) -> str:
    return f"{set_name}-{'batch' if study == 'A' else 'design'}-table.csv"


def table_rows(units: Sequence[Unit]) -> list[dict[str, str]]:
    """Batch table (Study A) or design table (Study B) rows; labels at indices 1..4 by ``|``."""
    rows = []
    for u in units:
        row = {
            "unit_id": u.unit_id,
            "set": u.set_name,
            "kind": u.kind,
            "sequence": str(u.sequence),
            "block_id": u.block_id,
            "block_position": str(u.block_position),
            "swap_w1_w4": "1" if u.swap_w1_w4 else "0",
            "family_first": u.family_first,
            "seed": u.seed,
            "seed_label": u.seed_label,
        }
        for f in FAMILIES:
            for r in ROLES:
                row[f"{f}_{r}"] = "|".join(u.permutation.labels(f, r))
        if isinstance(u, ABatch):
            row.update(profile=u.profile, designer=u.designer, atom_order="|".join(u.atom_order))
        if isinstance(u, BDyadSlot):
            row.update(sq_arm=u.sq_arm, structured_family=u.structured_family)
            for w, order in enumerate(u.wave_orders, start=1):
                row[f"V{w}_atom_order"] = "|".join(order)
        rows.append(row)
    return rows


def table_csv(units: Sequence[Unit]) -> bytes:
    columns = A_TABLE_COLUMNS if units and units[0].study == "A" else B_TABLE_COLUMNS
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    w.writerows(table_rows(units))
    return buf.getvalue().encode("utf-8")


def render_set(units: Sequence[Unit]) -> dict[str, bytes]:
    """All files of one study/set, keyed by POSIX path relative to ``<out>/<study>/``."""
    if not units:
        raise ValueError("no units")
    first = units[0]
    study, set_name = first.study, first.set_name
    files: dict[str, bytes] = {}
    for u in units:
        files[f"{u.unit_id}/curriculum.csv"] = curriculum_csv(u)
        files[f"{u.unit_id}/permutation.json"] = permutation_json(u)
    files[table_name(study, set_name)] = table_csv(units)
    rows = balance_rows(units)
    files[f"{set_name}-balance.csv"] = balance_csv(rows)
    manifest: dict[str, Any] = {
        "format": MANIFEST_FORMAT,
        "format_version": 1,
        "generator": {"name": "av-schedules", "version": __version__},
        "demo": first.demo,
        "seed_label": first.seed_label,
        "study": study,
        "set": set_name,
        "units": [u.unit_id for u in units if u.kind != "spare"],
        "spare_units": [u.unit_id for u in units if u.kind == "spare"],
        "files": {path: hashlib.sha256(data).hexdigest() for path, data in sorted(files.items())},
        "balance_max_abs_deviation": max_abs_deviation(rows),
    }
    files[f"{set_name}-manifest.json"] = dumps_json(manifest)
    return files


def generate(
    master: MasterSeed, study: Study, set_name: SetName, *, spares: int = 8
) -> dict[str, bytes]:
    """Build the units of one study/set and render all of its files."""
    return render_set(build_units(master, study, set_name, spares=spares))


def _stale_files(root: Path, files: Mapping[str, bytes]) -> list[Path]:
    """Files listed by a manifest being replaced that the new output no longer contains."""
    stale: list[Path] = []
    for rel in sorted(files):
        if not rel.endswith("-manifest.json"):
            continue
        old = root.joinpath(*PurePosixPath(rel).parts)
        if not old.is_file():
            continue
        try:
            listed = json.loads(old.read_text(encoding="utf-8")).get("files", {})
        except (ValueError, OSError) as exc:
            raise ValueError(f"cannot read existing manifest {old}: {exc}") from exc
        base = PurePosixPath(rel).parent
        for name in sorted(listed):
            parts = PurePosixPath(name).parts
            if ".." in parts or PurePosixPath(name).is_absolute():
                raise ValueError(f"unexpected path {name!r} in {old}")
            full = (base / name).as_posix()
            if full not in files:
                stale.append(root.joinpath(*PurePosixPath(full).parts))
    return stale


def write_files(root: Path, files: Mapping[str, bytes]) -> None:
    """Write ``files`` (relative POSIX paths) under ``root`` byte-for-byte.

    When a set manifest (``*-manifest.json``) is replaced, files listed by the old
    manifest but absent from the new output (e.g. unit folders of a set regenerated with
    fewer spares) are deleted, and emptied unit folders removed, so no stale units remain.
    """
    for path in _stale_files(root, files):
        if path.is_file():
            path.unlink()
        parent = path.parent
        if parent != root and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    for rel, data in sorted(files.items()):
        path = root.joinpath(*PurePosixPath(rel).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def existing_seed_label(study_dir: Path, set_name: SetName) -> str | None:
    """Seed label of a set already written to ``study_dir``, if any."""
    path = study_dir / f"{set_name}-manifest.json"
    if not path.is_file():
        return None
    label = json.loads(path.read_text(encoding="utf-8")).get("seed_label")
    return label if isinstance(label, str) else None


def demo_example_files() -> dict[str, bytes]:
    """The committed DEMO examples, keyed by path relative to ``schedules/examples/demo``.

    All set-level files for pilot and confirmatory sets of both studies, plus the unit
    folders listed in ``EXAMPLE_UNITS``.
    """
    master = demo_seed(EXAMPLE_DEMO_SEED)
    out: dict[str, bytes] = {}
    studies: tuple[Study, ...] = ("A", "B")
    for study in studies:
        for set_name in SET_NAMES:
            for rel, data in generate(master, study, set_name).items():
                unit = rel.split("/", 1)[0] if "/" in rel else None
                if unit is None or unit in EXAMPLE_UNITS:
                    out[f"{study}/{rel}"] = data
    return out
