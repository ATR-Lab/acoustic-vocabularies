"""Typed bank manifest and attempt summary, and the manifest derived from stored files.

The manifest format is the shared contract `av_generation.bank_manifest`
(`generation/schema/bank-manifest.schema.json`, `av-banks/bank-manifest` v1); this
module adds the typed reader/writer (`BankManifest`), the attempt summary
(`attempts/<n>/attempt.json`, `banks/schema/bank-attempt.schema.json`) and
`manifest_from_files`, which derives the whole manifest from the files of a bank
directory. The builder writes its manifest with that function, and `verify` recomputes
it the same way, so the bank hash is by construction the hash of what is stored:

- identity (`bank_id`, `bank_version`, `seed_namespace`) from the attempt summaries;
  `set` and `demo` from the bank ID; `dyad_slot`, `labels`, `atom_order` and
  `permutation_sha256` from the stored `permutation.json`;
- `generation_config_sha256` = `GenerationConfig.frozen_sha256()` of the stored
  `generation-config.json`;
- `attempts[]` from every `attempts/<n>/`: status, failed cell and wall time from
  `attempt.json`, `slots_used` (records) and `slots_sha256` (file hash) from
  `slots.jsonl`;
- `cells[]` (complete banks): per profile (P1, P2, P3) and atom in the stored order, the
  first 4 `valid` slot records of the attempt used, in slot order, with their recipes and
  waveform hashes; `file_sha256` is the hash of the WAV file on disk.

`to_dyad_bank` converts a complete manifest to `av_sound.dyad_bank.DyadBank`, the
provisional input of the dyad package builder (#13). Amendments are not applied there:
consumers apply `amendments.jsonl` with `av_generation.bank_manifest.effective_menu`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Final, Literal

from av_generation.bank_manifest import (
    BANK_MANIFEST_FORMAT,
    BANK_MANIFEST_VERSION,
    bank_sha256,
    effective_menu,
)
from av_generation.constants import B_OPTIONS_PER_CELL, B_SHOWN_OPTIONS, PROFILES
from av_generation.genconfig import GenerationConfig
from av_generation.ids import bank_set
from av_generation.jsonio import (
    CodecError,
    decode_dataclass,
    file_sha256,
    iter_jsonl,
    read_json,
    to_json_value,
    write_document,
)
from av_generation.outcomes import SlotOutcome
from av_generation.records import Document, SlotRecord, read_records
from av_sound.dyad_bank import BankHandoff, BankOption, DyadBank
from av_sound.recipe import Recipe
from jsonschema import Draft202012Validator

from av_banks._paths import schema_path
from av_banks.layout import BankLayout, option_id, option_wav
from av_banks.permutation import load_permutation

ATTEMPT_FORMAT: Final = "av-banks/bank-attempt"
ATTEMPT_VERSION: Final = 1
ATTEMPT_SCHEMA: Final = "bank-attempt.schema.json"


_SETS: Final[dict[str, Literal["demo", "pilot", "confirmatory"]]] = {
    "demo": "demo",
    "pilot": "pilot",
    "confirmatory": "confirmatory",
}


class ManifestError(ValueError):
    """A bank directory cannot give a manifest (missing, damaged or inconsistent files)."""


# ---------------------------------------------------------------------------
# Manifest (bank-manifest.schema.json)


@dataclass(frozen=True, slots=True)
class ManifestOption:
    """One retained option: rank 1-3 the displayed menu, rank 4 the reserve."""

    rank: int
    menu: Literal["shown", "reserve"]
    option_id: str
    recipe: Mapping[str, Any]
    recipe_sha256: str
    pcm_sha256: str
    file_sha256: str
    wav: str
    slot_id: str


@dataclass(frozen=True, slots=True)
class ManifestCell:
    """One atom under one profile: its 4 options and the slots the cell used."""

    profile: str
    atom_id: str
    slots_used: int
    options: tuple[ManifestOption, ...]


@dataclass(frozen=True, slots=True)
class CellRef:
    profile: str
    atom_id: str


@dataclass(frozen=True, slots=True)
class AttemptEntry:
    """One attempt as the manifest lists it."""

    attempt: int
    status: Literal["complete", "failed"]
    slots_used: int
    failed_cell: CellRef | None
    slots_sha256: str
    wall_ms: int


@dataclass(frozen=True, slots=True)
class BankManifest(Document):
    """`manifest.json` of a bank directory (`bank-manifest.schema.json`)."""

    TAG = BANK_MANIFEST_FORMAT
    VERSION = BANK_MANIFEST_VERSION
    SCHEMA = "bank-manifest.schema.json"

    bank_id: str
    set: Literal["demo", "pilot", "confirmatory"]
    demo: bool
    dyad_slot: str | None
    bank_version: str
    seed_namespace: str
    generation_config_sha256: str
    permutation_sha256: str | None
    status: Literal["complete", "unavailable"]
    attempt_used: int | None
    attempts: tuple[AttemptEntry, ...]
    labels: Mapping[str, str]
    atom_order: tuple[str, ...]
    cells: tuple[ManifestCell, ...]

    def bank_sha256(self) -> str:
        """The bank hash (`av_generation.bank_manifest.bank_sha256` of `to_dict()`)."""
        return bank_sha256(self.to_dict())

    def cell(self, profile: str, atom_id: str) -> ManifestCell:
        for cell in self.cells:
            if cell.profile == profile and cell.atom_id == atom_id:
                return cell
        raise KeyError(f"no cell {profile} {atom_id} in bank {self.bank_id}")

    def menu(
        self, amendments: Sequence[Mapping[str, Any]] = ()
    ) -> dict[tuple[str, str], tuple[str, ...]]:
        """`(profile, atom)` -> the 3 shown option IDs after `amendments`."""
        return effective_menu(self.to_dict(), amendments)


def read_manifest(path: str | os.PathLike[str]) -> BankManifest:
    """Read and schema-check a `manifest.json` (or the bank directory holding it)."""
    source = Path(path)
    if source.is_dir():
        source = BankLayout(source).manifest
    return BankManifest.read(source)


def read_amendments(path: str | os.PathLike[str]) -> tuple[dict[str, Any], ...]:
    """The lines of an `amendments.jsonl` (empty when the file does not exist)."""
    target = Path(path)
    if target.is_dir():
        target = BankLayout(target).amendments
    if not target.exists():
        return ()
    entries = []
    for entry in iter_jsonl(target):
        if not isinstance(entry, dict):
            raise ManifestError(f"{target}: every line is a JSON object")
        entries.append(entry)
    return tuple(entries)


# ---------------------------------------------------------------------------
# Attempt summary (banks/schema/bank-attempt.schema.json)


@cache
def _validator(name: str) -> Draft202012Validator:
    schema = read_json(schema_path(name))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def attempt_errors(doc: Mapping[str, Any]) -> tuple[str, ...]:
    """Schema errors of an `attempt.json` document (empty when valid)."""
    errors = []
    for err in _validator(ATTEMPT_SCHEMA).iter_errors(dict(doc)):
        where = "/".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(f"{where}: {err.message}")
    return tuple(sorted(errors))


@dataclass(frozen=True, slots=True)
class Stats:
    """Count and nearest-rank percentiles of a list of milliseconds."""

    n: int
    p50: int | None
    p95: int | None
    max: int | None


@dataclass(frozen=True, slots=True)
class Throughput:
    slots: int
    wall_ms: int
    slots_per_minute: float | None
    latency_ms: Stats
    """Model call time per slot (`SlotRecord.latency_ms`; slots without a call excluded)."""
    slot_ms: Stats
    """Open-to-close time per slot (`t_ms - t_open_ms`): call, parse, render, checks."""
    slots_over_cap: int
    """Slots whose open-to-close time exceeded the 40-s slot cap."""


@dataclass(frozen=True, slots=True)
class ProfileSummary:
    profile: str
    status: Literal["complete", "failed", "stopped", "not_started"]
    slots_used: int
    cells_complete: int
    wall_ms: int


@dataclass(frozen=True, slots=True)
class CellSummary:
    profile: str
    atom_id: str
    slots_used: int
    retained: int
    outcomes: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class AttemptSummary:
    """`attempts/<n>/attempt.json`: one attempt, complete or failed, kept on disk."""

    bank_id: str
    bank_version: str
    seed_namespace: str
    attempt: int
    status: Literal["complete", "failed"]
    reason: str | None
    slots_used: int
    slots_sha256: str
    failed_cell: CellRef | None
    failed_cells: tuple[CellRef, ...]
    workers: int
    t_start_ms: int
    t_end_ms: int
    wall_ms: int
    started_utc: str
    ended_utc: str
    profiles: tuple[ProfileSummary, ...]
    cells: tuple[CellSummary, ...]
    throughput: Throughput

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = to_json_value(self)
        data["format"] = ATTEMPT_FORMAT
        data["format_version"] = ATTEMPT_VERSION
        return data

    def check(self) -> AttemptSummary:
        errors = attempt_errors(self.to_dict())
        if errors:
            raise ManifestError(f"attempt summary does not match {ATTEMPT_SCHEMA}: {errors[:3]}")
        return self

    def write(self, path: str | os.PathLike[str]) -> str:
        """Validate and write the document (exclusive); returns the file SHA-256."""
        self.check()
        return write_document(path, self.to_dict(), exclusive=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AttemptSummary:
        errors = attempt_errors(data)
        if errors:
            raise ManifestError(f"attempt summary does not match {ATTEMPT_SCHEMA}: {errors[:3]}")
        body = {k: v for k, v in data.items() if k not in ("format", "format_version")}
        try:
            return decode_dataclass(cls, body)
        except CodecError as err:
            raise ManifestError(f"attempt summary: {err}") from err

    @classmethod
    def read(cls, path: str | os.PathLike[str]) -> AttemptSummary:
        return cls.from_dict(read_json(path))


# ---------------------------------------------------------------------------
# Manifest derived from the stored files


def _cell_options(
    layout: BankLayout, bank_id: str, profile: str, atom_id: str, records: Sequence[SlotRecord]
) -> ManifestCell:
    valid = [r for r in records if SlotOutcome(r.outcome) is SlotOutcome.VALID]
    options = []
    for rank, record in enumerate(valid[:B_OPTIONS_PER_CELL], start=1):
        wav = option_wav(profile, atom_id, rank)
        path = layout.option(wav)
        if not path.is_file():
            raise ManifestError(f"{bank_id}: option WAV {wav} is missing")
        if record.recipe is None or record.recipe_sha256 is None or record.pcm_sha256 is None:
            raise ManifestError(f"{record.slot_id}: a valid slot record without its recipe")
        options.append(
            ManifestOption(
                rank=rank,
                menu="shown" if rank <= B_SHOWN_OPTIONS else "reserve",
                option_id=option_id(bank_id, profile, atom_id, rank),
                recipe=dict(record.recipe),
                recipe_sha256=record.recipe_sha256,
                pcm_sha256=record.pcm_sha256,
                file_sha256=file_sha256(path),
                wav=wav,
                slot_id=record.slot_id,
            )
        )
    return ManifestCell(profile, atom_id, len(records), tuple(options))


def manifest_from_files(path: str | os.PathLike[str]) -> BankManifest:
    """Derive the bank manifest from the files of a bank directory (module docstring).

    Raises `ManifestError` for an unfinished build (no attempt, a missing summary, or a
    last attempt that failed before the 4th) or inconsistent identity fields. It checks
    the result against the schema but does not re-render: that is `verify`'s job.
    """
    layout = BankLayout(Path(path))
    numbers = layout.attempts()
    if not numbers:
        raise ManifestError(f"{layout.root}: no attempts")
    if numbers != tuple(range(1, len(numbers) + 1)):
        raise ManifestError(f"{layout.root}: attempts on disk are not 1..n: {numbers}")
    permutation = load_permutation(layout.permutation)
    config = GenerationConfig.read(layout.generation_config)
    summaries = []
    entries = []
    records_of: dict[int, list[SlotRecord]] = {}
    for number in numbers:
        summary_path = layout.attempt_summary(number)
        if not summary_path.is_file():
            raise ManifestError(f"attempt {number} has no {summary_path.name} (unfinished build)")
        summary = AttemptSummary.read(summary_path)
        records = read_records(layout.slots(number), SlotRecord)
        records_of[number] = records
        summaries.append(summary)
        entries.append(
            AttemptEntry(
                attempt=number,
                status=summary.status,
                slots_used=len(records),
                failed_cell=summary.failed_cell,
                slots_sha256=file_sha256(layout.slots(number)),
                wall_ms=summary.wall_ms,
            )
        )
    identity = {(s.bank_id, s.bank_version, s.seed_namespace) for s in summaries}
    if len(identity) != 1:
        raise ManifestError(f"attempt summaries disagree on the bank identity: {sorted(identity)}")
    bank_id, bank_version, seed_namespace = identity.pop()
    if bank_id != layout.bank_id:
        raise ManifestError(f"directory {layout.bank_id!r} holds attempts of bank {bank_id!r}")
    last = summaries[-1]
    if any(s.status == "complete" for s in summaries[:-1]):
        raise ManifestError("an attempt after a complete attempt (the first complete one is used)")
    cells: list[ManifestCell] = []
    if last.status == "complete":
        status: Literal["complete", "unavailable"] = "complete"
        attempt_used: int | None = last.attempt
        by_cell: dict[tuple[str, str], list[SlotRecord]] = {}
        for record in records_of[last.attempt]:
            by_cell.setdefault((record.profile.value, record.atom_id), []).append(record)
        for profile in PROFILES:
            for atom in permutation.atom_order:
                records = sorted(by_cell.get((profile, atom), []), key=lambda r: r.slot)
                cells.append(_cell_options(layout, bank_id, profile, atom, records))
    elif len(summaries) == 4:
        status, attempt_used = "unavailable", None
    else:
        raise ManifestError(
            f"attempt {last.attempt} failed and no later attempt exists (unfinished build)"
        )
    found_set = _SETS[bank_set(bank_id)]
    manifest = BankManifest(
        bank_id=bank_id,
        set=found_set,
        demo=found_set == "demo",
        dyad_slot=permutation.unit_id,
        bank_version=bank_version,
        seed_namespace=seed_namespace,
        generation_config_sha256=config.frozen_sha256(),
        permutation_sha256=permutation.sha256,
        status=status,
        attempt_used=attempt_used,
        attempts=tuple(entries),
        labels=dict(permutation.labels),
        atom_order=permutation.atom_order,
        cells=tuple(cells),
    )
    errors = manifest.schema_errors()
    if errors:
        raise ManifestError(f"derived manifest does not match its schema: {errors[:3]}")
    return manifest


# ---------------------------------------------------------------------------
# Package-builder input (#13)


def to_dyad_bank(manifest: BankManifest) -> DyadBank:
    """The manifest as `av_sound.dyad_bank.DyadBank` (complete banks only; option
    `source` = the slot ID, `file_sha256` = the option WAV's hash). Its `handoff` names
    this manifest (`av-banks/bank-manifest` v1 and the bank hash), so a package built
    from it records the #26 identity rather than the provisional one. Amendments are
    not applied (see the module docstring); `av_banks.handoff.qualified_dyad_bank`
    verifies the bank directory first and refuses an amended bank."""
    if manifest.status != "complete":
        raise ManifestError(f"bank {manifest.bank_id} is {manifest.status}; nothing to package")
    cells = {
        (cell.profile, cell.atom_id): tuple(
            BankOption(
                o.rank,
                Recipe.from_dict(o.recipe),
                o.pcm_sha256,
                o.slot_id,
                file_sha256=o.file_sha256,
            )
            for o in cell.options
        )
        for cell in manifest.cells
    }
    return DyadBank(
        bank_id=manifest.bank_id,
        demo=manifest.demo,
        labels=dict(manifest.labels),
        cells=cells,
        handoff=BankHandoff(BANK_MANIFEST_FORMAT, BANK_MANIFEST_VERSION, manifest.bank_sha256()),
    )
