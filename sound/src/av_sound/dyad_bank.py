"""PROVISIONAL Study B dyad-bank input for the package builder (#13).

The real bank format comes from the B bank builder (#26), which does not exist yet. This
module defines the smallest input the dyad package builder needs, so that #13 can be
built and tested now: per profile (P1, P2, P3) and atom (`K-a1` .. `Q-r4`), the four
retained options in rank order (ranks 1-3 are the displayed menu, rank 4 is the reserve;
Study B protocol §4), each with its recipe and expected waveform hash, plus the semantic
label bound to each atom (the dyad's stored permutation, #29).

Format `av-sound/provisional-bank`, version 1 (`sound/schema/provisional-bank.schema.json`).
When #26 lands, its manifest replaces this format: add a reader for it and keep this one
only for the tests. Option `source` values are opaque provenance (attempt and slot of the
bank builder); the package builder never copies them.

`synthetic_dyad_bank()` makes a DEMO bank from a fixed rule (no random numbers, no
model): format and pipeline tests only, not study material and not a learnable
vocabulary. Its options are distinct within each profile but have not been through the
bank compatibility checks (#26 `verify`).

Qualified #26 handoff: `av_banks.manifest.to_dyad_bank` (and `av_banks.handoff`) give a
`DyadBank` whose `handoff` names the verified #26 manifest (`av-banks/bank-manifest`
version 1 and its bank hash) and whose options carry the bank's WAV `file_sha256`. The
package then records that identity instead of the provisional one, so consumers (#70)
can check the package against the #26 manifest itself. A bank without `handoff` stays
explicitly provisional.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from av_sound._schemas import schema_validator, strict_loads
from av_sound.grammar import ATOM_IDS, parse_atom_id
from av_sound.recipe import TOTAL_MS, Profile, Recipe, StrictJsonError
from av_sound.renderer import MIN_EVENT_SAMPLES, event_samples, render
from av_sound.store import SEMANTIC_LABELS, InvalidIdentifier, canonical_json, check_book_id

BANK_FORMAT: Final = "av-sound/provisional-bank"
BANK_FORMAT_VERSION: Final = 1
BANK_SCHEMA: Final = "provisional-bank.schema.json"
OPTIONS_PER_CELL: Final = 4
"""Retained options per atom and profile: 3 shown + 1 reserve (Study B protocol §4)."""
SHOWN_OPTIONS: Final = 3
RESERVE_RANK: Final = 4
PROFILES: Final[tuple[Profile, ...]] = (Profile.P1, Profile.P2, Profile.P3)
SYNTHETIC_PREFIX: Final = "DEMO-"
QUALIFIED_BANK_FORMAT: Final = "av-banks/bank-manifest"
"""The #26 bank manifest format (`generation/schema/bank-manifest.schema.json`)."""
QUALIFIED_BANK_FORMAT_VERSION: Final = 1
_SHA256_RE: Final = re.compile(r"[0-9a-f]{64}")


class BankError(ValueError):
    """The provisional bank input is invalid; `.code` is `E_BANK`."""

    code = "E_BANK"


@dataclass(frozen=True, slots=True)
class BankHandoff:
    """The verified #26 bank a `DyadBank` was converted from: format, version and the
    bank hash (`av_generation.bank_manifest.bank_sha256` of the whole manifest)."""

    format: str
    format_version: int
    bank_sha256: str

    def __post_init__(self) -> None:
        if (self.format, self.format_version) != (
            QUALIFIED_BANK_FORMAT,
            QUALIFIED_BANK_FORMAT_VERSION,
        ):
            raise BankError(
                f"a qualified handoff is {QUALIFIED_BANK_FORMAT} version "
                f"{QUALIFIED_BANK_FORMAT_VERSION}, got {self.format} {self.format_version}"
            )
        if not _SHA256_RE.fullmatch(self.bank_sha256):
            raise BankError("a qualified handoff needs a lowercase SHA-256 bank hash")


@dataclass(frozen=True, slots=True)
class BankOption:
    """One retained option of a cell. Rank 1-3: displayed menu order; rank 4: reserve."""

    rank: int
    recipe: Recipe
    pcm_sha256: str
    source: str | None = field(default=None, compare=False)
    """Opaque provenance from the bank builder; never copied into a package."""
    file_sha256: str | None = field(default=None, compare=False)
    """SHA-256 of the bank's canonical WAV file (#26 manifest); the package's file must
    equal it. `None` for provisional banks, whose files the package builder writes."""

    @property
    def menu(self) -> str:
        """`shown` (ranks 1-3) or `reserve` (rank 4)."""
        return "reserve" if self.rank == RESERVE_RANK else "shown"


@dataclass(frozen=True, slots=True)
class DyadBank:
    """A frozen dyad bank: 16 atoms x 3 profiles x 4 options, and the atom meanings."""

    bank_id: str
    demo: bool
    labels: Mapping[str, str]
    """Atom ID -> semantic label (the dyad's permutation, #29)."""
    cells: Mapping[tuple[str, str], tuple[BankOption, ...]]
    """(profile, atom ID) -> the 4 options in rank order."""
    handoff: BankHandoff | None = field(default=None, compare=False)
    """The verified #26 manifest this bank came from; `None` keeps it provisional."""

    def __post_init__(self) -> None:
        try:
            check_book_id(self.bank_id)
        except InvalidIdentifier as err:
            raise BankError(str(err)) from err
        if self.demo != self.bank_id.startswith(SYNTHETIC_PREFIX):
            raise BankError("DEMO banks, and only they, have IDs starting with DEMO-")
        _check_labels(self.labels)
        expected = {(p.value, a) for p in PROFILES for a in ATOM_IDS}
        if set(self.cells) != expected:
            raise BankError("a bank has exactly one cell per profile and atom (48 cells)")
        for (profile, atom), options in sorted(self.cells.items()):
            ranks = [o.rank for o in options]
            if ranks != list(range(1, OPTIONS_PER_CELL + 1)):
                raise BankError(f"{profile} {atom}: options must have ranks 1-4 in order")
            if self.handoff is not None and not all(
                o.file_sha256 is not None and _SHA256_RE.fullmatch(o.file_sha256)
                for o in options
            ):
                raise BankError(
                    f"{profile} {atom}: a qualified bank names every option's file_sha256"
                )
        object.__setattr__(self, "labels", dict(self.labels))
        object.__setattr__(self, "cells", dict(self.cells))

    @property
    def qualified(self) -> bool:
        """True for a bank converted from a verified #26 manifest (`handoff` set)."""
        return self.handoff is not None

    def package_bank(self) -> dict[str, Any]:
        """The package manifest's `bank` record: the #26 manifest identity for a qualified
        bank, else the provisional format and `bank_sha256()`."""
        if self.handoff is not None:
            return {
                "format": self.handoff.format,
                "format_version": self.handoff.format_version,
                "bank_sha256": self.handoff.bank_sha256,
            }
        return {
            "format": BANK_FORMAT,
            "format_version": BANK_FORMAT_VERSION,
            "bank_sha256": self.bank_sha256(),
        }

    def option(self, profile: Profile | str, atom_id: str, rank: int) -> BankOption:
        """The option of rank 1..4 for an atom under a profile."""
        options = self.cells[(Profile(profile).value, atom_id)]
        if not 1 <= rank <= OPTIONS_PER_CELL:
            raise KeyError(f"rank {rank} is not 1..{OPTIONS_PER_CELL}")
        return options[rank - 1]

    def to_dict(self) -> dict[str, Any]:
        """The provisional bank document (`provisional-bank.schema.json`)."""
        cells = []
        for profile in PROFILES:
            for atom in ATOM_IDS:
                options = []
                for option in self.cells[(profile.value, atom)]:
                    entry: dict[str, Any] = {
                        "rank": option.rank,
                        "recipe": option.recipe.to_dict(),
                        "pcm_sha256": option.pcm_sha256,
                    }
                    if option.source is not None:
                        entry["source"] = option.source
                    options.append(entry)
                cells.append({"profile": profile.value, "atom_id": atom, "options": options})
        return {
            "format": BANK_FORMAT,
            "format_version": BANK_FORMAT_VERSION,
            "provisional": True,
            "bank_id": self.bank_id,
            "demo": self.demo,
            "labels": {atom: self.labels[atom] for atom in ATOM_IDS},
            "cells": cells,
        }

    def bank_sha256(self) -> str:
        """SHA-256 of the compact canonical JSON of `to_dict()`."""
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DyadBank:
        """Parse a provisional bank document; raises `BankError`."""
        errors = sorted(
            schema_validator(BANK_SCHEMA).iter_errors(dict(data)), key=lambda e: list(e.path)
        )
        if errors:
            where = "/".join(str(p) for p in errors[0].absolute_path) or "(root)"
            raise BankError(
                f"provisional bank does not match {BANK_SCHEMA} at {where}: {errors[0].message}"
            )
        cells: dict[tuple[str, str], tuple[BankOption, ...]] = {}
        for cell in data["cells"]:
            key = (cell["profile"], cell["atom_id"])
            if key in cells:
                raise BankError(f"duplicate cell {key[0]} {key[1]}")
            cells[key] = tuple(
                BankOption(
                    rank=o["rank"],
                    recipe=Recipe.from_dict(o["recipe"]),
                    pcm_sha256=o["pcm_sha256"],
                    source=o.get("source"),
                )
                for o in cell["options"]
            )
        return cls(
            bank_id=data["bank_id"], demo=data["demo"], labels=dict(data["labels"]), cells=cells
        )


def load_dyad_bank(path: str | os.PathLike[str]) -> DyadBank:
    """Read a provisional bank JSON file (strict JSON); raises `BankError`."""
    try:
        data = strict_loads(Path(path).read_bytes())
    except StrictJsonError as err:
        raise BankError(f"{path}: {err}") from err
    if not isinstance(data, dict):
        raise BankError(f"{path}: a bank document is a JSON object")
    return DyadBank.from_dict(data)


def _check_labels(labels: Mapping[str, str]) -> None:
    if set(labels) != set(ATOM_IDS):
        raise BankError("labels must name every atom K-a1 .. Q-r4 exactly once")
    for (family, role), allowed in SEMANTIC_LABELS.items():
        bound = [labels[a] for a in ATOM_IDS if a.startswith(family + "-" + role[0])]
        if sorted(bound) != sorted(allowed):
            raise BankError(
                f"{family} {role} labels {bound} are not a permutation of {list(allowed)}"
            )


# ---------------------------------------------------------------------------
# Synthetic DEMO bank

_PITCH_RULE: Final[tuple[tuple[int, int], ...]] = ((5, 3), (7, 1), (11, 8))
_RHYTHMS: Final[tuple[tuple[int, int, int], ...]] = (
    (2, 1, 3),
    (1, 3, 2),
    (3, 2, 1),
    (1, 2, 3),
    (3, 1, 2),
    (2, 3, 1),
    (1, 1, 2),
    (2, 2, 1),
    (1, 2, 2),
    (4, 2, 3),
    (3, 4, 2),
    (2, 4, 3),
)
_GAPS: Final[tuple[tuple[int, int], ...]] = (
    (40, 20),
    (20, 60),
    (60, 40),
    (20, 20),
    (40, 60),
    (60, 20),
    (20, 40),
    (60, 60),
    (40, 40),
)
_AMPLITUDES: Final[tuple[tuple[float, float, float], ...]] = (
    (0.8, 1.0, 0.6),
    (1.0, 0.6, 0.8),
    (0.6, 0.8, 1.0),
    (1.0, 0.8, 0.6),
    (0.8, 0.6, 1.0),
    (0.6, 1.0, 0.8),
)


def _synthetic_recipe(k: int, total_ms: int) -> Recipe:
    gaps = _GAPS[k % len(_GAPS)]
    for step in range(len(_RHYTHMS)):
        weights = _RHYTHMS[(k + step) % len(_RHYTHMS)]
        if min(event_samples(total_ms, weights, gaps)) >= MIN_EVENT_SAMPLES:
            break
    else:  # pragma: no cover - (1, 1, 2) and (2, 2, 1) fit every length and gap pair
        raise AssertionError("no admissible rhythm")
    p1, p2, p3 = (((k * m + o) % 13) - 6 for m, o in _PITCH_RULE)
    return Recipe(total_ms, (p1, p2, p3), weights, gaps, _AMPLITUDES[k % len(_AMPLITUDES)])


def identity_labels() -> dict[str, str]:
    """Atom ID -> label with matrix index i bound to the i-th ontology label."""
    labels = {}
    for atom in ATOM_IDS:
        ref = parse_atom_id(atom)
        labels[atom] = SEMANTIC_LABELS[(ref.family, ref.role)][ref.index - 1]
    return labels


def synthetic_dyad_bank(
    bank_id: str = "DEMO-DYAD-01", labels: Mapping[str, str] | None = None
) -> DyadBank:
    """A DEMO bank: 16 atoms x 3 profiles x 4 options from a fixed rule (not study material).

    Candidate k (counting from 64 x profile position) gets pitches, gaps, amplitudes
    and rhythm from k; its length cycles with the atom index, the profile and the rank,
    so the 16 option combinations of a message have several lengths. A candidate that
    overflows, has a short event or repeats a waveform of the profile is skipped and the
    rule moves on to the next k. `labels` defaults to the
    identity permutation (`identity_labels()`).
    """
    if not bank_id.startswith(SYNTHETIC_PREFIX):
        raise BankError("synthetic banks have IDs starting with DEMO-")
    cells: dict[tuple[str, str], tuple[BankOption, ...]] = {}
    for p_index, profile in enumerate(PROFILES):
        seen: set[str] = set()
        k = 64 * p_index
        for atom in ATOM_IDS:
            index = parse_atom_id(atom).index
            options: list[BankOption] = []
            for rank in range(1, OPTIONS_PER_CELL + 1):
                total_ms = TOTAL_MS[(index - 1 + p_index + rank - 1) % len(TOTAL_MS)]
                while True:
                    recipe = _synthetic_recipe(k, total_ms)
                    k += 1
                    rendered = render(recipe, profile)
                    if rendered.overflow or rendered.short_event:  # pragma: no cover
                        continue
                    if rendered.pcm_sha256 in seen:  # pragma: no cover
                        continue
                    break
                seen.add(rendered.pcm_sha256)
                options.append(
                    BankOption(rank, recipe, rendered.pcm_sha256, f"demo-{profile.value}-{k:03d}")
                )
            cells[(profile.value, atom)] = tuple(options)
    return DyadBank(
        bank_id=bank_id,
        demo=True,
        labels=dict(labels) if labels is not None else identity_labels(),
        cells=cells,
    )
