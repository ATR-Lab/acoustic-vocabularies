"""Bank registers, register checks and the set guard for loading a bank (#27; #28 reuses).

A register is a CSV file (UTF-8, `\\n` line ends, one header row, `REGISTER_COLUMNS`)
with one row per bank built: main banks and spares, complete and unavailable. It names
each bank's dyad slot, the config hash it was built under, the attempt used and its bank
hash, and which bank each dyad slot uses. Paths are POSIX paths relative to the register's
root, so a register stays valid when the root moves (restricted storage).

`register_problems` recomputes every bank hash from the stored files
(`manifest.manifest_from_files`) and compares it, and every other column, with the row.
`open_bank(bank_dir, mode=...)` is the one way tooling loads a bank for a set: a pilot
bank (`bank-P...`, or anything else that is not `bank-C...`) is refused in confirmatory
mode with a `bank_manifest.BankSetError` that names the bank and its set.
"""

from __future__ import annotations

import csv
import os
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Final, Literal, cast

from av_generation.bank_manifest import BankSetError, require_bank_set
from av_generation.genconfig import GenerationConfig
from av_generation.ids import IdError, bank_set
from av_generation.jsonio import file_sha256
from av_generation.records import RunManifest
from av_generation.rundir import MANIFEST_NAME as RUN_MANIFEST_NAME

from av_banks.layout import BankLayout
from av_banks.manifest import BankManifest, ManifestError, manifest_from_files, read_manifest
from av_banks.verify import verify_bank

SetName = Literal["demo", "pilot", "confirmatory"]
Role = Literal["main", "spare"]

REGISTER_COLUMNS: Final[tuple[str, ...]] = (
    "bank_id",
    "bank_version",
    "role",
    "dyad_slot",
    "set",
    "status",
    "use",
    "attempt_used",
    "attempts",
    "slots_used",
    "verify",
    "reason",
    "seed_namespace",
    "generation_config_sha256",
    "separation_threshold",
    "permutation_sha256",
    "bank_sha256",
    "run_id",
    "bank_path",
)
"""Register columns in file order. `use` is `1` for the bank its dyad slot uses (at most
one per slot: the first complete bank that verifies), else `0`; `verify` is `pass` or
`fail` (`banks verify`); `attempt_used` is empty for an unavailable bank."""


class RegisterError(ValueError):
    """A register file is malformed, or a bank cannot give a register row."""


@dataclass(frozen=True, slots=True)
class RegisterRow:
    """One register row (see `REGISTER_COLUMNS`)."""

    bank_id: str
    bank_version: str
    role: Role
    dyad_slot: str
    set: SetName
    status: Literal["complete", "unavailable"]
    use: bool
    attempt_used: int | None
    attempts: int
    slots_used: int
    verify: Literal["pass", "fail"]
    reason: str
    seed_namespace: str
    generation_config_sha256: str
    separation_threshold: str
    permutation_sha256: str
    bank_sha256: str
    run_id: str
    bank_path: str

    def values(self) -> list[str]:
        """The row as CSV cells, in `REGISTER_COLUMNS` order."""
        out = []
        for name in REGISTER_COLUMNS:
            value = getattr(self, name)
            if isinstance(value, bool):
                out.append("1" if value else "0")
            elif value is None:
                out.append("")
            else:
                out.append(str(value))
        return out

    @classmethod
    def from_values(cls, cells: dict[str, str], where: str = "row") -> RegisterRow:
        """Parse one CSV row (column name -> text); raises `RegisterError`."""

        def integer(name: str, *, empty: bool = False) -> int | None:
            text = cells[name]
            if empty and text == "":
                return None
            if not text.isdigit():
                raise RegisterError(f"{where}: {name} must be a whole number, got {text!r}")
            return int(text)

        def choice(name: str, allowed: Sequence[str]) -> str:
            if cells[name] not in allowed:
                raise RegisterError(f"{where}: {name} must be one of {list(allowed)}")
            return cells[name]

        if cells["use"] not in ("0", "1"):
            raise RegisterError(f"{where}: use must be 0 or 1")
        attempts = integer("attempts")
        slots = integer("slots_used")
        assert attempts is not None and slots is not None
        return cls(
            bank_id=cells["bank_id"],
            bank_version=cells["bank_version"],
            role=cast(Role, choice("role", ("main", "spare"))),
            dyad_slot=cells["dyad_slot"],
            set=cast(SetName, choice("set", ("demo", "pilot", "confirmatory"))),
            status=cast(
                Literal["complete", "unavailable"], choice("status", ("complete", "unavailable"))
            ),
            use=cells["use"] == "1",
            attempt_used=integer("attempt_used", empty=True),
            attempts=attempts,
            slots_used=slots,
            verify=cast(Literal["pass", "fail"], choice("verify", ("pass", "fail"))),
            reason=cells["reason"],
            seed_namespace=cells["seed_namespace"],
            generation_config_sha256=cells["generation_config_sha256"],
            separation_threshold=cells["separation_threshold"],
            permutation_sha256=cells["permutation_sha256"],
            bank_sha256=cells["bank_sha256"],
            run_id=cells["run_id"],
            bank_path=cells["bank_path"],
        )


assert tuple(f.name for f in fields(RegisterRow)) == REGISTER_COLUMNS


# ---------------------------------------------------------------------------
# Loading a bank for a set


def open_bank(bank_dir: str | os.PathLike[str], *, mode: SetName) -> BankManifest:
    """Read a bank for use in `mode` (`pilot`, `confirmatory` or `demo`).

    Refuses (`BankSetError`) a bank of another set, so a confirmatory-mode load of a pilot
    bank fails before anything else is read, and a bank whose `manifest.json` does not
    hash to its `bank-sha256.txt` (`ManifestError`). It does not re-render (`verify`).
    """
    layout = BankLayout(Path(bank_dir))
    manifest = read_manifest(layout.manifest)
    require_bank_set(manifest.to_dict(), mode)
    stored = layout.bank_hash.read_text(encoding="utf-8").strip()
    if stored != manifest.bank_sha256():
        raise ManifestError(
            f"bank {manifest.bank_id}: manifest.json hashes to {manifest.bank_sha256()}, "
            f"{layout.bank_hash.name} holds {stored}"
        )
    return manifest


def check_bank_id_set(bank_id: str, mode: SetName) -> None:
    """Refuse a bank ID of another set (`BankSetError`), e.g. any pilot ID in
    confirmatory mode; anything that is not a bank ID (such as `PILOT-B-01`) is refused
    too."""
    try:
        found = bank_set(bank_id)
    except IdError as err:
        raise BankSetError(f"{err}; {mode} mode refuses it") from None
    if found != mode:
        raise BankSetError(f"bank {bank_id!r} is a {found} bank; {mode} mode refuses it")


# ---------------------------------------------------------------------------
# Rows from banks


def run_id_of(bank_dir: str | os.PathLike[str]) -> str:
    """The run ID of a bank inside a run directory (`<run>/banks/<bank_id>`)."""
    run_manifest = Path(bank_dir).parent.parent / RUN_MANIFEST_NAME
    try:
        return RunManifest.read(run_manifest).run_id
    except (OSError, ValueError) as err:
        raise RegisterError(f"{Path(bank_dir).name}: no run manifest ({err})") from None


def bank_reason(manifest: BankManifest) -> str:
    """Why a bank ended as it did, from its attempt list (no commas: CSV friendly)."""
    failed = [
        f"t{a.attempt} {a.failed_cell.profile} {a.failed_cell.atom_id}"
        for a in manifest.attempts
        if a.status == "failed" and a.failed_cell is not None
    ]
    if manifest.status == "complete":
        text = f"complete at attempt {manifest.attempt_used}"
        return text + (f"; failed cells: {'; '.join(failed)}" if failed else "")
    return (
        f"unavailable: {len(manifest.attempts)} attempts failed (cells: {'; '.join(failed)}); "
        "never assign"
    )


def relative_path(path: Path, root: Path) -> str:
    """`path` relative to `root` as a POSIX path (refuses paths outside `root`)."""
    try:
        rel = Path(os.path.abspath(path)).relative_to(os.path.abspath(root))
    except ValueError:
        raise RegisterError(f"{path} is not inside the register root {root}") from None
    return rel.as_posix()


def register_row(
    bank_dir: str | os.PathLike[str],
    *,
    root: str | os.PathLike[str],
    role: Role,
    use: bool,
    verify_ok: bool,
) -> RegisterRow:
    """The register row of one built bank (its stored manifest, config and run)."""
    layout = BankLayout(Path(bank_dir))
    try:
        manifest = read_manifest(layout.manifest)
        config = GenerationConfig.read(layout.generation_config)
    except (OSError, ValueError) as err:
        raise RegisterError(f"{layout.bank_id}: {err}") from None
    if manifest.dyad_slot is None or manifest.permutation_sha256 is None:
        raise RegisterError(f"{manifest.bank_id}: the manifest names no dyad slot")
    return RegisterRow(
        bank_id=manifest.bank_id,
        bank_version=manifest.bank_version,
        role=role,
        dyad_slot=manifest.dyad_slot,
        set=manifest.set,
        status=manifest.status,
        use=use,
        attempt_used=manifest.attempt_used,
        attempts=len(manifest.attempts),
        slots_used=sum(a.slots_used for a in manifest.attempts),
        verify="pass" if verify_ok else "fail",
        reason=bank_reason(manifest),
        seed_namespace=manifest.seed_namespace,
        generation_config_sha256=manifest.generation_config_sha256,
        separation_threshold=config.separation_threshold,
        permutation_sha256=manifest.permutation_sha256,
        bank_sha256=manifest.bank_sha256(),
        run_id=run_id_of(layout.root),
        bank_path=relative_path(layout.root, Path(root)),
    )


# ---------------------------------------------------------------------------
# File


def write_register(rows: Iterable[RegisterRow], path: str | os.PathLike[str]) -> str:
    """Write a register (header and rows in the given order); returns its SHA-256."""
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(REGISTER_COLUMNS)
        for row in rows:
            writer.writerow(row.values())
    return file_sha256(path)


def read_register(path: str | os.PathLike[str]) -> tuple[RegisterRow, ...]:
    """Read and parse a register; raises `RegisterError` for a malformed file."""
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header is None or tuple(header) != REGISTER_COLUMNS:
            raise RegisterError(f"{Path(path).name}: the header must be {list(REGISTER_COLUMNS)}")
        rows = []
        for number, cells in enumerate(reader, start=2):
            if len(cells) != len(REGISTER_COLUMNS):
                raise RegisterError(f"line {number}: {len(cells)} cells")
            rows.append(
                RegisterRow.from_values(
                    dict(zip(REGISTER_COLUMNS, cells, strict=True)), f"line {number}"
                )
            )
    return tuple(rows)


# ---------------------------------------------------------------------------
# Checks


def _row_problems(row: RegisterRow, root: Path, expected_set: SetName, rerun: bool) -> list[str]:
    where = f"{row.bank_id} v{row.bank_version}"
    problems: list[str] = []
    try:
        check_bank_id_set(row.bank_id, expected_set)
    except BankSetError as err:
        problems.append(f"{where}: {err}")
    if row.set != expected_set:
        problems.append(f"{where}: set {row.set!r}, the register holds {expected_set} banks")
    bank_dir = root.joinpath(*row.bank_path.split("/"))
    layout = BankLayout(bank_dir)
    try:
        manifest = read_manifest(layout.manifest)
        recomputed = manifest_from_files(layout.root).bank_sha256()
        config = GenerationConfig.read(layout.generation_config)
        written = layout.bank_hash.read_text(encoding="utf-8").strip()
    except (OSError, ValueError) as err:
        return [*problems, f"{where}: {row.bank_path}: {err}"]
    if row.bank_sha256 != recomputed:
        problems.append(f"{where}: register hash {row.bank_sha256} != recomputed {recomputed}")
    if manifest.bank_sha256() != recomputed or written != recomputed:
        problems.append(f"{where}: stored manifest or bank-sha256.txt differs from the files")
    expected = {
        "bank_id": manifest.bank_id,
        "bank_version": manifest.bank_version,
        "dyad_slot": manifest.dyad_slot,
        "set": manifest.set,
        "status": manifest.status,
        "attempt_used": manifest.attempt_used,
        "attempts": len(manifest.attempts),
        "slots_used": sum(a.slots_used for a in manifest.attempts),
        "seed_namespace": manifest.seed_namespace,
        "generation_config_sha256": manifest.generation_config_sha256,
        "permutation_sha256": manifest.permutation_sha256,
        "separation_threshold": config.separation_threshold,
    }
    for name, value in expected.items():
        if getattr(row, name) != value:
            problems.append(f"{where}: {name} {getattr(row, name)!r} != stored {value!r}")
    try:
        if row.run_id != run_id_of(layout.root):
            problems.append(f"{where}: run_id differs from the run manifest")
    except RegisterError as err:
        problems.append(f"{where}: {err}")
    if row.use and (row.status != "complete" or row.verify != "pass"):
        problems.append(f"{where}: only a complete bank that verifies can be used")
    if rerun:
        report = verify_bank(layout.root)
        if report.ok != (row.verify == "pass"):
            problems.append(f"{where}: verify now {'passes' if report.ok else 'fails'}")
        problems.extend(f"{where}: verify: {p}" for p in report.problems[:5])
    return problems


def register_problems(
    path: str | os.PathLike[str],
    root: str | os.PathLike[str],
    *,
    expected_set: SetName,
    rerun_verify: bool = False,
) -> tuple[str, ...]:
    """Every problem of a register against the banks under `root` (empty when sound).

    For each row: the bank ID and set belong to `expected_set`; the bank hash recomputed
    from the stored files equals the register's, the stored manifest's and
    `bank-sha256.txt`; every other column equals the stored manifest, config and run
    manifest; `use` only for a complete bank that verifies. Across rows: bank and version
    unique, at most one used bank per dyad slot. `rerun_verify` re-runs `banks verify`.
    """
    base = Path(root)
    try:
        rows = read_register(path)
    except (OSError, RegisterError) as err:
        return (f"register: {err}",)
    problems: list[str] = []
    keys = Counter((r.bank_id, r.bank_version) for r in rows)
    problems.extend(f"{b} v{v}: listed {n} times" for (b, v), n in sorted(keys.items()) if n > 1)
    used = Counter(r.dyad_slot for r in rows if r.use)
    problems.extend(f"{slot}: {n} banks in use" for slot, n in sorted(used.items()) if n > 1)
    for row in rows:
        problems.extend(_row_problems(row, base, expected_set, rerun_verify))
    return tuple(problems)
