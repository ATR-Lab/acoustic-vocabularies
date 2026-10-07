"""Seed checks of the confirmatory banks: unique across the 72 banks, disjoint from the pilot.

A bank's model seeds are `seeds.derive_seed` of its B seed keys
`B|<seed_namespace>|<attempt>|<profile>|<atom>|<slot>` (#26). Its *key space* is every
key it may use under the budget: 4 attempts x 3 profiles x 16 atoms x 12 slots = 2,304
keys, so the 72 banks span 165,888 keys. Before the run (`check_seeds`):

- every namespace names its own bank and version (`builder.seed_namespace_error`) and its
  bank belongs to the expected set (`confirmatory`, or `demo` for a rehearsal);
- namespaces are unique, and the 165,888 keys give 165,888 distinct 64-bit seeds;
- no namespace and no seed is shared with the pilot: the pilot's namespaces come from
  its bank directories, run directories or campaign directory (manifests, attempt
  summaries and every slot record's seed key) or from a CSV with a `seed_namespace`
  column, and the pilot seeds are the whole key space of those namespaces (which
  contains every seed a pilot slot record used: `read_pilot` checks each record's seed).

After the run (`check_used_seeds`): every slot record of every campaign bank (all
versions, crashed builds included) has a key of its own bank's namespace, the seed of
that key, a seed no other record used, and no pilot seed.
"""

from __future__ import annotations

import csv
import os
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from av_generation.constants import B_MAX_ATTEMPTS, B_SLOTS_PER_CELL, PROFILES
from av_generation.ids import IdError, bank_set
from av_generation.jsonio import file_sha256, iter_jsonl, read_json, to_json_value
from av_generation.seeds import b_seed_key, parse_seed_key, seed_from_key, seeds_digest
from av_sound.grammar import ATOM_IDS

from av_banks.builder import seed_namespace_error
from av_banks.layout import BankLayout

from .common import E_INPUT, CampaignError

KEYS_PER_BANK = B_MAX_ATTEMPTS * len(PROFILES) * len(ATOM_IDS) * B_SLOTS_PER_CELL
"""2,304 seed keys per bank version (the budget's key space)."""
MAX_LISTED = 20
"""At most this many examples of a problem are listed in a report."""


def bank_seed_keys(namespace: str) -> tuple[str, ...]:
    """The 2,304 B seed keys a bank with this namespace may use, in traversal order
    (attempt, profile, atom in `ATOM_IDS` order, slot)."""
    return tuple(
        b_seed_key(namespace, attempt, profile, atom, slot)
        for attempt in range(1, B_MAX_ATTEMPTS + 1)
        for profile in PROFILES
        for atom in ATOM_IDS
        for slot in range(1, B_SLOTS_PER_CELL + 1)
    )


def key_namespace(seed_key: str) -> str:
    """The namespace part of a B seed key."""
    parsed = parse_seed_key(seed_key)
    if parsed.namespace.value != "B":
        raise ValueError(f"not a B seed key: {seed_key!r}")
    return parsed.parts[0]


@dataclass(frozen=True, slots=True)
class SeedEntry:
    """One bank version whose seeds are checked."""

    bank_id: str
    bank_version: str
    seed_namespace: str


@dataclass(frozen=True, slots=True)
class SourceRef:
    """A pilot input: its kind, its name (last path component) and the SHA-256 of the file
    the namespaces were read from (`None` for a literal namespace)."""

    kind: str
    name: str
    sha256: str | None


@dataclass(frozen=True, slots=True)
class PilotBankTiming:
    """Throughput of one pilot bank (from its attempt summaries), for sizing the run."""

    bank_id: str
    slots: int
    wall_ms: int
    slot_ms_p95: int | None


@dataclass(frozen=True, slots=True)
class PilotSeeds:
    """The pilot namespaces the confirmatory seeds must avoid."""

    namespaces: frozenset[str]
    sources: tuple[SourceRef, ...] = ()
    timings: tuple[PilotBankTiming, ...] = ()
    problems: tuple[str, ...] = ()
    records_checked: int = 0


@dataclass(frozen=True, slots=True)
class SeedCheck:
    """The result of `check_seeds` (`ok` exactly when every list of problems is empty)."""

    banks: int
    namespaces: int
    keys: int
    distinct_seeds: int
    seeds_digest: str
    """`seeds.seeds_digest` of every key in entry order (a cross-machine check)."""
    namespace_problems: tuple[str, ...]
    duplicate_namespaces: tuple[str, ...]
    seed_collisions: tuple[str, ...]
    pilot_namespaces: int
    pilot_keys: int
    pilot_namespace_overlap: tuple[str, ...]
    pilot_seed_overlap: tuple[str, ...]
    pilot_problems: tuple[str, ...]
    pilot_sources: tuple[SourceRef, ...]
    ok: bool

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = to_json_value(self)
        return data


def _pilot_seeds(namespaces: Iterable[str]) -> dict[int, str]:
    seeds: dict[int, str] = {}
    for namespace in sorted(namespaces):
        for key in bank_seed_keys(namespace):
            seeds.setdefault(seed_from_key(key), key)
    return seeds


def check_seeds(entries: Sequence[SeedEntry], *, expected_set: str, pilot: PilotSeeds) -> SeedCheck:
    """Check the seeds of `entries` before the run (see the module docstring)."""
    namespace_problems: list[str] = []
    for entry in entries:
        try:
            found = bank_set(entry.bank_id)
        except IdError as err:
            namespace_problems.append(str(err))
            continue
        if found != expected_set:
            namespace_problems.append(f"{entry.bank_id} is a {found} bank, not {expected_set}")
        error = seed_namespace_error(entry.bank_id, entry.bank_version, entry.seed_namespace)
        if error is not None:
            namespace_problems.append(error)
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.seed_namespace] = counts.get(entry.seed_namespace, 0) + 1
    duplicates = tuple(sorted(ns for ns, n in counts.items() if n > 1))
    keys: list[str] = []
    seen: dict[int, str] = {}
    collisions: list[str] = []
    for namespace in dict.fromkeys(e.seed_namespace for e in entries):
        for key in bank_seed_keys(namespace):
            keys.append(key)
            seed = seed_from_key(key)
            other = seen.setdefault(seed, key)
            if other != key:
                collisions.append(f"{other} and {key} share seed {seed}")
    pilot_seeds = _pilot_seeds(pilot.namespaces)
    overlap_ns = tuple(sorted(set(counts) & pilot.namespaces))
    overlap_seeds = [
        f"{key} has the seed of pilot key {pilot_seeds[seed]}"
        for seed, key in seen.items()
        if seed in pilot_seeds
    ]
    check = SeedCheck(
        banks=len({e.bank_id for e in entries}),
        namespaces=len(counts),
        keys=len(keys),
        distinct_seeds=len(seen),
        seeds_digest=seeds_digest(keys),
        namespace_problems=tuple(namespace_problems[:MAX_LISTED]),
        duplicate_namespaces=duplicates,
        seed_collisions=tuple(collisions[:MAX_LISTED]),
        pilot_namespaces=len(pilot.namespaces),
        pilot_keys=len(pilot.namespaces) * KEYS_PER_BANK,
        pilot_namespace_overlap=overlap_ns,
        pilot_seed_overlap=tuple(sorted(overlap_seeds)[:MAX_LISTED]),
        pilot_problems=pilot.problems[:MAX_LISTED],
        pilot_sources=pilot.sources,
        ok=False,
    )
    ok = not (
        namespace_problems
        or duplicates
        or collisions
        or overlap_ns
        or overlap_seeds
        or pilot.problems
    )
    return SeedCheck(**{**_fields(check), "ok": ok})


def _fields(check: SeedCheck) -> dict[str, Any]:
    return {name: getattr(check, name) for name in SeedCheck.__dataclass_fields__}


# ---------------------------------------------------------------------------
# Pilot inputs


@dataclass
class _PilotReader:
    namespaces: set[str] = field(default_factory=set)
    sources: list[SourceRef] = field(default_factory=list)
    timings: list[PilotBankTiming] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    records: int = 0

    def bank(self, bank_dir: Path) -> None:
        layout = BankLayout(bank_dir)
        if layout.manifest.is_file():
            manifest = read_json(layout.manifest)
            if isinstance(manifest, dict) and isinstance(manifest.get("seed_namespace"), str):
                self.namespaces.add(manifest["seed_namespace"])
            self.sources.append(
                SourceRef("bank_manifest", bank_dir.name, file_sha256(layout.manifest))
            )
        slots = 0
        wall = 0
        p95: list[int] = []
        for attempt in layout.attempts():
            summary_path = layout.attempt_summary(attempt)
            if summary_path.is_file():
                summary = read_json(summary_path)
                if isinstance(summary, dict):
                    if isinstance(summary.get("seed_namespace"), str):
                        self.namespaces.add(summary["seed_namespace"])
                    throughput = summary.get("throughput") or {}
                    slots += int(throughput.get("slots") or 0)
                    wall += int(throughput.get("wall_ms") or 0)
                    value = (throughput.get("slot_ms") or {}).get("p95")
                    if isinstance(value, int):
                        p95.append(value)
            if layout.slots(attempt).is_file():
                self.slot_log(layout.slots(attempt))
        if slots:
            self.timings.append(PilotBankTiming(bank_dir.name, slots, wall, max(p95, default=None)))

    def slot_log(self, path: Path) -> None:
        for record in iter_jsonl(path):
            if not isinstance(record, dict) or not isinstance(record.get("seed_key"), str):
                continue
            self.records += 1
            key = record["seed_key"]
            try:
                self.namespaces.add(key_namespace(key))
            except (ValueError, IdError) as err:
                self.problems.append(f"{path.parent.parent.parent.name}: {err}")
                continue
            if record.get("seed") != seed_from_key(key):
                self.problems.append(f"{key}: recorded seed is not the seed of its key")

    def csv(self, path: Path) -> None:
        with open(path, encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or "seed_namespace" not in reader.fieldnames:
                raise CampaignError(
                    E_INPUT, f"{path.name}: a pilot CSV needs a seed_namespace column"
                )
            for row in reader:
                value = (row.get("seed_namespace") or "").strip()
                if value:
                    self.namespaces.add(value)
        self.sources.append(SourceRef("csv", path.name, file_sha256(path)))


def _bank_dirs(path: Path) -> Iterator[Path]:
    """Bank directories under a bank, run or campaign directory (sorted)."""
    if (path / "manifest.json").is_file() or (path / "attempts").is_dir():
        yield path
        return
    if (path / "banks").is_dir():
        yield from sorted(p for p in (path / "banks").iterdir() if p.is_dir())
        return
    if (path / "runs").is_dir():
        for run in sorted(p for p in (path / "runs").iterdir() if p.is_dir()):
            yield from _bank_dirs(run)
        return
    raise CampaignError(
        E_INPUT,
        f"{path.name}: not a bank, run or campaign directory (no manifest.json, banks/ or runs/)",
    )


def read_pilot(
    paths: Sequence[str | os.PathLike[str]] = (), *, namespaces: Sequence[str] = ()
) -> PilotSeeds:
    """The pilot namespaces from bank, run or campaign directories, CSV files with a
    `seed_namespace` column, and literal namespaces."""
    reader = _PilotReader()
    for item in paths:
        path = Path(item)
        if path.is_file() and path.suffix.lower() == ".csv":
            reader.csv(path)
        elif path.is_dir():
            for bank_dir in _bank_dirs(path):
                reader.bank(bank_dir)
        else:
            raise CampaignError(E_INPUT, f"{path.name}: no such pilot directory or CSV file")
    for namespace in namespaces:
        reader.namespaces.add(namespace)
        reader.sources.append(SourceRef("namespace", namespace, None))
    return PilotSeeds(
        namespaces=frozenset(reader.namespaces),
        sources=tuple(reader.sources),
        timings=tuple(reader.timings),
        problems=tuple(reader.problems),
        records_checked=reader.records,
    )


# ---------------------------------------------------------------------------
# After the run


@dataclass(frozen=True, slots=True)
class UsedSeeds:
    """The seeds the campaign's slot records used (`check_used_seeds`)."""

    bank_versions: int
    records: int
    distinct_seeds: int
    problems: tuple[str, ...]
    ok: bool

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = to_json_value(self)
        return data


def check_used_seeds(
    banks: Mapping[str, tuple[SeedEntry, Path]], *, pilot_namespaces: Iterable[str]
) -> UsedSeeds:
    """Check every slot record of the given bank directories (`label -> (entry, dir)`)."""
    pilot = _pilot_seeds(pilot_namespaces)
    seen: dict[int, str] = {}
    problems: list[str] = []
    records = 0
    for label in sorted(banks):
        entry, bank_dir = banks[label]
        layout = BankLayout(bank_dir)
        for attempt in layout.attempts():
            path = layout.slots(attempt)
            if not path.is_file():
                continue
            for record in iter_jsonl(path):
                records += 1
                key = record.get("seed_key") if isinstance(record, dict) else None
                seed = record.get("seed") if isinstance(record, dict) else None
                if not isinstance(key, str) or not isinstance(seed, int):
                    problems.append(f"{label}: a slot record has no seed key or seed")
                    continue
                try:
                    namespace = key_namespace(key)
                except (ValueError, IdError) as err:
                    problems.append(f"{label}: {err}")
                    continue
                if namespace != entry.seed_namespace:
                    problems.append(f"{label}: {key} is not in namespace {entry.seed_namespace}")
                if seed != seed_from_key(key):
                    problems.append(f"{label}: {key} recorded seed {seed}, not its key's seed")
                if seed in seen:
                    problems.append(f"{label}: {key} reuses the seed of {seen[seed]}")
                else:
                    seen[seed] = key
                if seed in pilot:
                    problems.append(f"{label}: {key} uses the seed of pilot key {pilot[seed]}")
    return UsedSeeds(
        bank_versions=len(banks),
        records=records,
        distinct_seeds=len(seen),
        problems=tuple(problems[:MAX_LISTED]),
        ok=not problems,
    )
