"""Fallback banks and fallback books (#15; Study A protocol §3.7).

Before confirmatory generation, each profile gets a frozen ordered **bank** of 64
admissible recipes and one complete 16-atom **fallback book**. Both come from code
and a stored seed only, never from rated candidates or learner data.

- **Bank**: draw recipes uniformly from the recipe domain (Study A protocol §3.2,
  §3.5) and keep a draw when `validate()` accepts it (reserved signals and the
  separation threshold included) against the recipes already kept. Stop at 64.
  Bank order is acceptance order.
- **Book**: a separate stream, the same greedy rule, stop at 16. The `j`-th accepted
  recipe goes to atom slot `ATOM_IDS[j]` (the stored order). All 120 pairs pass.
- **Scan** (`scan_fallback`): for an atom with no eligible candidate after 12 slots,
  the first unused bank recipe (lowest index) that passes the current book's checks;
  `None` means the whole book is replaced by the fallback book. The scan log is a
  separate record, not one of the 12 slots.

Every draw is a pure function of `(seed, profile, purpose, draw)`: a SHA-256 counter
stream that other languages can reproduce (`sound/docs/fallback.md`). The real seed and
the real banks and books are study material: they live in restricted storage and
only `fallback_bank_hash` is published. Seeds starting with `DEMO-` are public
examples.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import stat
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Final, Protocol

from av_sound._paths import data_root
from av_sound._schemas import load_json_file, schema_validator
from av_sound.features import ThresholdLike, format_fraction, parse_threshold
from av_sound.grammar import ATOM_IDS
from av_sound.recipe import AMPLITUDES, GAPS_MS, PITCHES, RHYTHM_WEIGHTS, TOTAL_MS, Profile, Recipe
from av_sound.renderer import RENDERER_VERSION, render
from av_sound.reserved import ReservedEntry, ReservedRegistry, load_reserved_registry
from av_sound.store import VocabularyStore, canonical_json, snapshot_digest
from av_sound.validate import (
    VALIDATOR_VERSION,
    Reference,
    ValidationResult,
    load_separation_threshold,
    validate,
)
from av_sound.wav import file_sha256, write_wav

BUILDER_VERSION: Final = "0.1.0"
"""Bumped whenever a bank or book can change for some seed (pilot until G4)."""
MANIFEST_VERSION: Final = 1
"""Version of the manifest format (`fallback-manifest.schema.json`)."""
SCAN_VERSION: Final = 1
"""Version of the scan record format (`fallback-scan.schema.json`)."""
STREAM_ID: Final = "av-sound/fallback/v1"
"""Key prefix of the SHA-256 counter stream (`sound/docs/fallback.md`)."""
FINGERPRINT_ID: Final = "av-sound/fallback/seed/v1"
"""Key prefix of the seed fingerprint."""
BANK_SIZE: Final = 64
BOOK_SIZE: Final = len(ATOM_IDS)
PURPOSES: Final[tuple[str, ...]] = ("bank", "book")
PROFILE_ORDER: Final[tuple[Profile, ...]] = (Profile.P1, Profile.P2, Profile.P3)
MAX_DRAWS: Final = 100_000
"""Draws allowed per bank or book before the build fails (`E_EXHAUSTED`)."""
DEMO_PREFIX: Final = "DEMO-"
DEMO_SEED: Final = "DEMO-fallback-v1"
"""The public example seed (sound/testvectors/fallback/demo-manifest.json)."""
MIN_SEED_LENGTH: Final = 32
"""Minimum length of a restricted (non-DEMO) seed, e.g. `secrets.token_hex(32)`."""
MANIFEST_NAME: Final = "fallback-manifest.json"
BUILD_LOG_NAME: Final = "fallback-build-log.json"

# Errors (`FallbackError.code`).
E_SEED: Final = "E_SEED"
E_EXHAUSTED: Final = "E_EXHAUSTED"
E_MANIFEST: Final = "E_MANIFEST"
E_VERSION: Final = "E_VERSION"
E_STALE: Final = "E_STALE"
E_POLICY: Final = "E_POLICY"
E_EXISTS: Final = "E_EXISTS"
E_INTEGRITY: Final = "E_INTEGRITY"

_SEED_RE: Final = re.compile(r"[A-Za-z0-9._-]{1,128}")


class FallbackError(ValueError):
    """A fallback build, manifest, scan or write failed; `.code` names the reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SupportsReference(Protocol):
    """A committed entry that can give its validator reference (e.g. `StoreEntry`)."""

    def reference(self) -> Reference: ...


# ---------------------------------------------------------------------------
# Seeds and the deterministic stream


def is_demo_seed(seed: str) -> bool:
    """True for a public example seed (`DEMO-...`)."""
    return isinstance(seed, str) and seed.startswith(DEMO_PREFIX)


def check_seed(seed: str) -> str:
    """Return `seed` if it is acceptable, else raise `FallbackError` (`E_SEED`).

    1-128 ASCII letters, digits, `.`, `_` and `-`. A restricted seed (not `DEMO-`)
    needs at least 32 characters so its fingerprint cannot be brute-forced; use
    `secrets.token_hex(32)` and keep it in restricted storage.
    """
    if not isinstance(seed, str) or not _SEED_RE.fullmatch(seed):
        raise FallbackError(E_SEED, "a seed is 1-128 ASCII letters, digits, '.', '_' and '-'")
    if not is_demo_seed(seed) and len(seed) < MIN_SEED_LENGTH:
        raise FallbackError(
            E_SEED,
            f"a restricted seed needs at least {MIN_SEED_LENGTH} characters "
            "(for example secrets.token_hex(32)); public examples start with DEMO-",
        )
    return seed


def seed_fingerprint(seed: str) -> str:
    """SHA-256 of `av-sound/fallback/seed/v1|<seed>` (ASCII): identifies a seed without
    revealing it."""
    return hashlib.sha256(f"{FINGERPRINT_ID}|{check_seed(seed)}".encode("ascii")).hexdigest()


def draw_key(seed: str, profile: Profile | str, purpose: str, draw: int) -> bytes:
    """Stream key of one draw: `av-sound/fallback/v1|<seed>|<profile>|<purpose>|<draw>`."""
    if purpose not in PURPOSES:
        raise ValueError(f"purpose must be one of {PURPOSES}, got {purpose!r}")
    if isinstance(draw, bool) or not isinstance(draw, int) or draw < 0:
        raise ValueError(f"draw must be a non-negative integer, got {draw!r}")
    prof = Profile(profile).value
    return f"{STREAM_ID}|{check_seed(seed)}|{prof}|{purpose}|{draw}".encode("ascii")


def stream_bytes(key: bytes) -> Iterator[int]:
    """The SHA-256 counter stream: `SHA-256(key || uint64_le(k))` for k = 0, 1, ...,
    concatenated, one byte at a time."""
    for k in itertools.count():
        yield from hashlib.sha256(key + k.to_bytes(8, "little")).digest()


def uniform_index(stream: Iterator[int], n: int) -> int:
    """Uniform integer in `[0, n)` (1 <= n <= 256) by rejection: take the next byte `b`;
    if `b < 256 - 256 % n`, return `b % n`, else take another byte."""
    limit = 256 - 256 % n
    for byte in stream:
        if byte < limit:
            return byte % n
    raise AssertionError("unreachable: the stream is infinite")  # pragma: no cover


def draw_recipe(seed: str, profile: Profile | str, purpose: str, draw: int) -> Recipe:
    """Recipe of one draw, uniform over the recipe domain.

    The 12 coordinates are drawn independently from one stream in recipe field order:
    `total_ms`, 3 pitches, 3 rhythm weights, 2 gaps, 3 amplitudes, each by
    `uniform_index` over its allowed values in ascending order.
    """
    stream = stream_bytes(draw_key(seed, profile, purpose, draw))

    def pick(values: Sequence[Any], count: int) -> list[Any]:
        return [values[uniform_index(stream, len(values))] for _ in range(count)]

    total_ms = pick(TOTAL_MS, 1)[0]
    pitches = pick(PITCHES, 3)
    weights = pick(RHYTHM_WEIGHTS, 3)
    gaps = pick(GAPS_MS, 2)
    amplitudes = pick(AMPLITUDES, 3)
    return Recipe(total_ms, tuple(pitches), tuple(weights), tuple(gaps), tuple(amplitudes))


# ---------------------------------------------------------------------------
# Values


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def reserved_digest(entries: Iterable[ReservedEntry]) -> str:
    """SHA-256 of the compact canonical JSON list of reserved entries (the store's
    `reserved_sha256`)."""
    return _sha256(canonical_json([e.to_dict() for e in entries]))


def references_digest(refs: Iterable[Reference]) -> str:
    """SHA-256 of `[[ref_id, recipe_sha256, pcm_sha256], ...]` (compact canonical JSON)."""
    return _sha256(canonical_json([[r.ref_id, r.recipe.sha256(), r.pcm_sha256] for r in refs]))


@dataclass(frozen=True, slots=True)
class DrawRecord:
    """One draw of a bank or book build and the validator's decision."""

    profile: Profile
    purpose: str
    draw: int
    recipe: Recipe
    codes: tuple[str, ...]
    position: int | None
    """Bank index or book position when accepted, else `None`."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.value,
            "purpose": self.purpose,
            "draw": self.draw,
            "recipe": self.recipe.to_dict(),
            "recipe_sha256": self.recipe.sha256(),
            "codes": list(self.codes),
            "accepted_as": self.position,
        }


@dataclass(frozen=True, slots=True)
class BankEntry:
    """One recipe of a fallback bank. `index` is its bank position (0 = scanned first)."""

    profile: Profile
    index: int
    draw: int
    recipe: Recipe
    pcm_sha256: str
    file_sha256: str

    @property
    def ref_id(self) -> str:
        """`bank-07`: the reference ID used while building the bank."""
        return f"bank-{self.index:02d}"

    @property
    def source(self) -> str:
        """Store `source` for a commit of this recipe: `fallback-bank-P1-07`."""
        return f"fallback-bank-{self.profile.value}-{self.index:02d}"

    def reference(self) -> Reference:
        return Reference(self.ref_id, self.recipe, self.pcm_sha256, self.profile)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "draw": self.draw,
            "recipe": self.recipe.to_dict(),
            "recipe_sha256": self.recipe.sha256(),
            "pcm_sha256": self.pcm_sha256,
            "file_sha256": self.file_sha256,
        }


@dataclass(frozen=True, slots=True)
class BookAtom:
    """One atom of a fallback book; `position` is its stored order (`ATOM_IDS[position]`)."""

    profile: Profile
    position: int
    atom_id: str
    draw: int
    recipe: Recipe
    pcm_sha256: str
    file_sha256: str

    @property
    def source(self) -> str:
        """Store `source` for a commit of this atom: `fallback-book-P1-K-a1`."""
        return f"fallback-book-{self.profile.value}-{self.atom_id}"

    def reference(self) -> Reference:
        return Reference(self.atom_id, self.recipe, self.pcm_sha256, self.profile)

    def to_dict(self) -> dict[str, Any]:
        return {
            "atom_id": self.atom_id,
            "position": self.position,
            "draw": self.draw,
            "recipe": self.recipe.to_dict(),
            "recipe_sha256": self.recipe.sha256(),
            "pcm_sha256": self.pcm_sha256,
            "file_sha256": self.file_sha256,
        }


@dataclass(frozen=True, slots=True)
class FallbackBank:
    """The frozen ordered bank of one profile (64 entries)."""

    profile: Profile
    threshold: Fraction
    entries: tuple[BankEntry, ...]
    draws: int
    """Draws consumed until the last acceptance."""
    log: tuple[DrawRecord, ...] = field(default=(), compare=False, repr=False)
    """Every draw with its codes (empty for a bank loaded from a manifest)."""

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[BankEntry]:
        return iter(self.entries)

    def __getitem__(self, index: int) -> BankEntry:
        return self.entries[index]

    @property
    def bank_sha256(self) -> str:
        """SHA-256 of `{"entries": [[recipe_sha256, pcm_sha256], ...], "profile": P}`."""
        rows = [[e.recipe.sha256(), e.pcm_sha256] for e in self.entries]
        return _sha256(canonical_json({"entries": rows, "profile": self.profile.value}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "bank_sha256": self.bank_sha256,
            "draws": self.draws,
            "entries": [e.to_dict() for e in self.entries],
        }


@dataclass(frozen=True, slots=True)
class FallbackBook:
    """The complete 16-atom fallback book of one profile, in stored order."""

    profile: Profile
    threshold: Fraction
    atoms: tuple[BookAtom, ...]
    draws: int
    log: tuple[DrawRecord, ...] = field(default=(), compare=False, repr=False)

    def __len__(self) -> int:
        return len(self.atoms)

    def __iter__(self) -> Iterator[BookAtom]:
        return iter(self.atoms)

    @property
    def book_sha256(self) -> str:
        """`snapshot_digest({atom_id: pcm_sha256})`: equals the `snapshot_sha256` of the
        frozen store book."""
        return snapshot_digest({a.atom_id: a.pcm_sha256 for a in self.atoms})

    def atom(self, atom_id: str) -> BookAtom:
        for a in self.atoms:
            if a.atom_id == atom_id:
                return a
        raise KeyError(atom_id)

    def recipes(self) -> dict[str, Recipe]:
        """Atom ID -> recipe, in stored order."""
        return {a.atom_id: a.recipe for a in self.atoms}

    def references(self) -> tuple[Reference, ...]:
        return tuple(a.reference() for a in self.atoms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "book_sha256": self.book_sha256,
            "draws": self.draws,
            "atoms": [a.to_dict() for a in self.atoms],
        }


def fallback_bank_hash(manifest: Mapping[str, Any]) -> str:
    """SHA-256 of the compact canonical JSON of `manifest` without `fallback_bank_hash`
    (sorted keys, separators `,` `:`, ASCII). The apparatus-manifest value."""
    return _sha256(canonical_json({k: v for k, v in manifest.items() if k != "fallback_bank_hash"}))


@dataclass(frozen=True, slots=True)
class FallbackSet:
    """Three banks and three books (P1, P2, P3) built from one seed and threshold."""

    seed_fingerprint: str
    demo_seed: str | None
    """The seed itself for a public `DEMO-` build; `None` for a restricted seed."""
    threshold: Fraction
    reserved_sha256: str
    banks: tuple[FallbackBank, ...]
    books: tuple[FallbackBook, ...]
    renderer_version: str = RENDERER_VERSION
    validator_version: str = VALIDATOR_VERSION
    builder_version: str = BUILDER_VERSION

    def bank(self, profile: Profile | str) -> FallbackBank:
        return self.banks[PROFILE_ORDER.index(Profile(profile))]

    def book(self, profile: Profile | str) -> FallbackBook:
        return self.books[PROFILE_ORDER.index(Profile(profile))]

    def manifest(self) -> dict[str, Any]:
        """The manifest (`fallback-manifest.schema.json`), `fallback_bank_hash` included.

        For a restricted seed the manifest is restricted (it lists the recipes); only
        `fallback_bank_hash` is published.
        """
        body: dict[str, Any] = {
            "manifest_version": MANIFEST_VERSION,
            "builder_version": self.builder_version,
            "stream": STREAM_ID,
            "seed_kind": "restricted" if self.demo_seed is None else "demo",
            "demo_seed": self.demo_seed,
            "seed_fingerprint": self.seed_fingerprint,
            "threshold": format_fraction(self.threshold),
            "renderer_version": self.renderer_version,
            "validator_version": self.validator_version,
            "reserved_sha256": self.reserved_sha256,
            "bank_size": BANK_SIZE,
            "profiles": [
                {"profile": bank.profile.value, "bank": bank.to_dict(), "book": book.to_dict()}
                for bank, book in zip(self.banks, self.books, strict=True)
            ],
        }
        body["fallback_bank_hash"] = fallback_bank_hash(body)
        return body

    @property
    def fallback_bank_hash(self) -> str:
        return str(self.manifest()["fallback_bank_hash"])

    def draw_logs(self) -> tuple[tuple[DrawRecord, ...], ...]:
        """Draw logs of the P1..P3 banks, then the P1..P3 books (empty when loaded)."""
        return tuple(b.log for b in self.banks) + tuple(k.log for k in self.books)

    def build_log(self) -> dict[str, Any]:
        """Every draw of every bank and book with its codes (restricted like the manifest)."""
        draws = [r.to_dict() for log in self.draw_logs() for r in log]
        return {
            "fallback_bank_hash": self.fallback_bank_hash,
            "seed_fingerprint": self.seed_fingerprint,
            "draws": draws,
        }

    def summary(self) -> dict[str, Any]:
        """Counts and digests only (safe to print for a restricted build)."""
        profiles = []
        for bank, book in zip(self.banks, self.books, strict=True):
            rejected: dict[str, int] = {}
            for record in (*bank.log, *book.log):
                for code in record.codes:
                    rejected[code] = rejected.get(code, 0) + 1
            profiles.append(
                {
                    "profile": bank.profile.value,
                    "bank_entries": len(bank),
                    "bank_draws": bank.draws,
                    "bank_sha256": bank.bank_sha256,
                    "book_atoms": len(book),
                    "book_draws": book.draws,
                    "book_sha256": book.book_sha256,
                    "rejection_codes": dict(sorted(rejected.items())),
                }
            )
        return {
            "seed_kind": "restricted" if self.demo_seed is None else "demo",
            "seed_fingerprint": self.seed_fingerprint,
            "threshold": format_fraction(self.threshold),
            "renderer_version": self.renderer_version,
            "validator_version": self.validator_version,
            "builder_version": self.builder_version,
            "reserved_sha256": self.reserved_sha256,
            "profiles": profiles,
            "fallback_bank_hash": self.fallback_bank_hash,
        }


# ---------------------------------------------------------------------------
# Building


ReservedLike = ReservedRegistry | Iterable[ReservedEntry] | None


def _reserved(
    reserved: ReservedLike,
) -> tuple[ReservedRegistry | tuple[ReservedEntry, ...], str]:
    if reserved is None:
        reserved = load_reserved_registry()
    if isinstance(reserved, ReservedRegistry):
        return reserved, reserved_digest(reserved.entries)
    entries = tuple(reserved)
    return entries, reserved_digest(entries)


def _threshold(threshold: ThresholdLike | None) -> Fraction:
    return load_separation_threshold() if threshold is None else parse_threshold(threshold)


def _build_threshold(threshold: ThresholdLike | None) -> Fraction:
    """The build threshold; it must have an exact decimal form (recorded as text)."""
    limit = _threshold(threshold)
    if "/" in format_fraction(limit):
        raise ValueError(f"threshold {limit} has no exact decimal form; use e.g. '0.10'")
    return limit


@dataclass(frozen=True, slots=True)
class _Accepted:
    draw: int
    recipe: Recipe
    pcm_sha256: str
    file_sha256: str


def _greedy(
    seed: str,
    profile: Profile,
    purpose: str,
    size: int,
    threshold: Fraction,
    reserved: ReservedRegistry | tuple[ReservedEntry, ...],
    max_draws: int,
) -> tuple[list[_Accepted], list[DrawRecord], int]:
    """Draw until `size` recipes pass `validate()` against those already kept."""
    kept: list[Reference] = []
    accepted: list[_Accepted] = []
    log: list[DrawRecord] = []
    for draw in range(max_draws):
        recipe = draw_recipe(seed, profile, purpose, draw)
        result = validate(recipe, profile, kept, reserved=reserved, threshold=threshold)
        position = len(accepted) if result.ok else None
        log.append(DrawRecord(profile, purpose, draw, recipe, result.codes, position))
        if result.ok:
            assert result.rendered is not None and result.pcm_sha256 is not None
            ref_id = f"{purpose}-{len(accepted):02d}"
            kept.append(Reference(ref_id, recipe, result.pcm_sha256, profile))
            accepted.append(
                _Accepted(draw, recipe, result.pcm_sha256, file_sha256(result.rendered))
            )
            if len(accepted) == size:
                return accepted, log, draw + 1
    raise FallbackError(
        E_EXHAUSTED,
        f"{profile.value} {purpose}: only {len(accepted)} of {size} recipes accepted in "
        f"{max_draws} draws",
    )


def build_bank(
    seed: str,
    profile: Profile | str,
    *,
    threshold: ThresholdLike | None = None,
    reserved: ReservedLike = None,
    max_draws: int = MAX_DRAWS,
) -> FallbackBank:
    """The 64-recipe bank of `profile`: draws from the `bank` stream, each kept when it is
    admissible (reserved signals included) and separated from every recipe kept before.

    `threshold=None` uses `sound/config/validator.json`; `reserved=None` the registry.
    """
    prof = Profile(profile)
    limit = _build_threshold(threshold)
    registry, _ = _reserved(reserved)
    accepted, log, draws = _greedy(
        check_seed(seed), prof, "bank", BANK_SIZE, limit, registry, max_draws
    )
    entries = tuple(
        BankEntry(prof, i, a.draw, a.recipe, a.pcm_sha256, a.file_sha256)
        for i, a in enumerate(accepted)
    )
    return FallbackBank(prof, limit, entries, draws, tuple(log))


def build_book(
    seed: str,
    profile: Profile | str,
    *,
    threshold: ThresholdLike | None = None,
    reserved: ReservedLike = None,
    max_draws: int = MAX_DRAWS,
) -> FallbackBook:
    """The 16-atom fallback book of `profile`: the greedy rule on the separate `book`
    stream; the `j`-th accepted recipe is atom `ATOM_IDS[j]`. Every pair of atoms
    passes the duplicate and separation checks."""
    prof = Profile(profile)
    limit = _build_threshold(threshold)
    registry, _ = _reserved(reserved)
    accepted, log, draws = _greedy(
        check_seed(seed), prof, "book", BOOK_SIZE, limit, registry, max_draws
    )
    atoms = tuple(
        BookAtom(prof, j, ATOM_IDS[j], a.draw, a.recipe, a.pcm_sha256, a.file_sha256)
        for j, a in enumerate(accepted)
    )
    return FallbackBook(prof, limit, atoms, draws, tuple(log))


def build_fallback(
    seed: str,
    *,
    threshold: ThresholdLike | None = None,
    reserved: ReservedLike = None,
    max_draws: int = MAX_DRAWS,
) -> FallbackSet:
    """Banks and books for P1, P2 and P3 from one seed (deterministic)."""
    check_seed(seed)
    limit = _build_threshold(threshold)
    registry, digest = _reserved(reserved)
    banks = tuple(
        build_bank(seed, p, threshold=limit, reserved=registry, max_draws=max_draws)
        for p in PROFILE_ORDER
    )
    books = tuple(
        build_book(seed, p, threshold=limit, reserved=registry, max_draws=max_draws)
        for p in PROFILE_ORDER
    )
    return FallbackSet(
        seed_fingerprint=seed_fingerprint(seed),
        demo_seed=seed if is_demo_seed(seed) else None,
        threshold=limit,
        reserved_sha256=digest,
        banks=banks,
        books=books,
    )


# ---------------------------------------------------------------------------
# Loading and verifying a manifest


def _manifest_dict(source: Mapping[str, Any] | str | os.PathLike[str]) -> dict[str, Any]:
    if isinstance(source, Mapping):
        return dict(source)
    data = load_json_file(Path(source))
    if not isinstance(data, dict):
        raise FallbackError(E_MANIFEST, "a fallback manifest is a JSON object")
    return data


def load_fallback(source: Mapping[str, Any] | str | os.PathLike[str]) -> FallbackSet:
    """Read a manifest (a decoded object or a file path) into a `FallbackSet`.

    Checks the schema, `fallback_bank_hash`, every `recipe_sha256`, `bank_sha256` and
    `book_sha256`, the entry order and the renderer and validator versions (`E_VERSION`:
    rebuild the set instead of using stale hashes). It does not render; use
    `verify_fallback` for that. Draw logs are not in the manifest and stay empty.
    """
    data = _manifest_dict(source)
    errors = sorted(
        schema_validator("fallback-manifest.schema.json").iter_errors(data),
        key=lambda e: (list(map(str, e.absolute_path)), e.message),
    )
    if errors:
        detail = "; ".join(
            f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors[:3]
        )
        raise FallbackError(E_MANIFEST, f"manifest does not match its schema: {detail}")
    if fallback_bank_hash(data) != data["fallback_bank_hash"]:
        raise FallbackError(E_MANIFEST, "fallback_bank_hash does not match the manifest")
    for name, current in (
        ("renderer_version", RENDERER_VERSION),
        ("validator_version", VALIDATOR_VERSION),
    ):
        if data[name] != current:
            raise FallbackError(
                E_VERSION, f"manifest {name} is {data[name]}, current is {current}; rebuild"
            )
    if [p["profile"] for p in data["profiles"]] != [p.value for p in PROFILE_ORDER]:
        raise FallbackError(E_MANIFEST, "profiles must be P1, P2, P3 in this order")
    limit = parse_threshold(data["threshold"])
    banks: list[FallbackBank] = []
    books: list[FallbackBook] = []
    for part in data["profiles"]:
        prof = Profile(part["profile"])
        entries = []
        for i, raw in enumerate(part["bank"]["entries"]):
            recipe = _manifest_recipe(raw, f"{prof.value} bank entry {i}")
            if raw["index"] != i:
                raise FallbackError(
                    E_MANIFEST, f"{prof.value} bank entry {i} has index {raw['index']}"
                )
            entries.append(
                BankEntry(prof, i, raw["draw"], recipe, raw["pcm_sha256"], raw["file_sha256"])
            )
        _check_draws(entries, part["bank"]["draws"], f"{prof.value} bank")
        bank = FallbackBank(prof, limit, tuple(entries), part["bank"]["draws"])
        if bank.bank_sha256 != part["bank"]["bank_sha256"]:
            raise FallbackError(E_MANIFEST, f"{prof.value} bank_sha256 does not match")
        atoms = []
        for j, raw in enumerate(part["book"]["atoms"]):
            recipe = _manifest_recipe(raw, f"{prof.value} book atom {j}")
            if raw["position"] != j or raw["atom_id"] != ATOM_IDS[j]:
                raise FallbackError(
                    E_MANIFEST, f"{prof.value} book atom {j} must be {ATOM_IDS[j]} at position {j}"
                )
            atoms.append(
                BookAtom(
                    prof,
                    j,
                    raw["atom_id"],
                    raw["draw"],
                    recipe,
                    raw["pcm_sha256"],
                    raw["file_sha256"],
                )
            )
        _check_draws(atoms, part["book"]["draws"], f"{prof.value} book")
        book = FallbackBook(prof, limit, tuple(atoms), part["book"]["draws"])
        if book.book_sha256 != part["book"]["book_sha256"]:
            raise FallbackError(E_MANIFEST, f"{prof.value} book_sha256 does not match")
        banks.append(bank)
        books.append(book)
    loaded = FallbackSet(
        seed_fingerprint=data["seed_fingerprint"],
        demo_seed=data["demo_seed"],
        threshold=limit,
        reserved_sha256=data["reserved_sha256"],
        banks=tuple(banks),
        books=tuple(books),
        renderer_version=data["renderer_version"],
        validator_version=data["validator_version"],
        builder_version=data["builder_version"],
    )
    if canonical_json(loaded.manifest()) != canonical_json(data):
        raise FallbackError(E_MANIFEST, "manifest is not in canonical form (rebuild it)")
    return loaded


def _check_draws(items: Sequence[BankEntry | BookAtom], draws: int, where: str) -> None:
    """Accepted draws increase strictly and the last one ends the stream (`draws`)."""
    numbers = [item.draw for item in items]
    if any(b <= a for a, b in itertools.pairwise(numbers)) or numbers[-1] + 1 != draws:
        raise FallbackError(E_MANIFEST, f"{where}: draw numbers do not match draws={draws}")


def _manifest_recipe(raw: Mapping[str, Any], where: str) -> Recipe:
    recipe = Recipe.from_dict(raw["recipe"])
    if recipe.sha256() != raw["recipe_sha256"] or recipe.to_dict() != raw["recipe"]:
        raise FallbackError(E_MANIFEST, f"{where}: recipe_sha256 does not match the recipe")
    return recipe


def verify_fallback(
    fallback: FallbackSet | Mapping[str, Any] | str | os.PathLike[str],
    *,
    reserved: ReservedLike = None,
) -> tuple[str, ...]:
    """Re-render and re-check a fallback set; return every problem (empty when sound).

    For each profile: every bank and book recipe renders to its recorded `pcm_sha256`
    and `file_sha256`; each bank entry is admissible against the earlier entries
    (acceptance order) with the set's threshold and the reserved signals, and so is
    each book atom against the earlier atoms (so all 64 x 63 / 2 bank pairs and all
    120 book pairs pass); the bank has 64 entries and the book 16 atoms; the reserved
    signals have the recorded digest. G4 (#25) uses this with the frozen renderer.
    """
    fset = fallback if isinstance(fallback, FallbackSet) else load_fallback(fallback)
    registry, digest = _reserved(reserved)
    problems: list[str] = []
    if digest != fset.reserved_sha256:
        problems.append("the reserved signals differ from the ones the set was built with")
    for bank, book in zip(fset.banks, fset.books, strict=True):
        prof = bank.profile.value
        if len(bank) != BANK_SIZE:
            problems.append(f"{prof} bank has {len(bank)} entries, expected {BANK_SIZE}")
        if len(book) != BOOK_SIZE:
            problems.append(f"{prof} book has {len(book)} atoms, expected {BOOK_SIZE}")
        items: list[tuple[str, list[tuple[str, Recipe, str, str]]]] = [
            ("bank", [(e.ref_id, e.recipe, e.pcm_sha256, e.file_sha256) for e in bank]),
            ("book", [(a.atom_id, a.recipe, a.pcm_sha256, a.file_sha256) for a in book]),
        ]
        for part, rows in items:
            kept: list[Reference] = []
            for ref_id, recipe, pcm_hash, file_hash in rows:
                result = validate(
                    recipe, bank.profile, kept, reserved=registry, threshold=fset.threshold
                )
                if result.pcm_sha256 != pcm_hash:
                    problems.append(f"{prof} {part} {ref_id}: re-render gives another waveform")
                elif result.rendered is not None and file_sha256(result.rendered) != file_hash:
                    problems.append(f"{prof} {part} {ref_id}: file_sha256 does not match")
                if not result.ok:
                    problems.append(
                        f"{prof} {part} {ref_id}: not admissible against the earlier "
                        f"entries ({', '.join(result.codes)})"
                    )
                kept.append(Reference(ref_id, recipe, pcm_hash, bank.profile))
    return tuple(problems)


# ---------------------------------------------------------------------------
# Scanning a bank for one atom


@dataclass(frozen=True, slots=True)
class ScanStep:
    """One bank recipe the scan looked at, in index order."""

    index: int
    recipe_sha256: str
    pcm_sha256: str
    outcome: str
    """`used` (skipped, no check), `rejected` (failed the book's checks) or `selected`."""
    codes: tuple[str, ...]
    messages: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "recipe_sha256": self.recipe_sha256,
            "pcm_sha256": self.pcm_sha256,
            "outcome": self.outcome,
            "codes": list(self.codes),
            "messages": list(self.messages),
        }


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Outcome of `scan_fallback`: the selected bank entry (or `None`) and the full log.

    `selected is None` means no unused bank recipe passes: substitute the complete
    fallback book and flag the book `failed_generation` (Study A protocol §3.7).
    """

    profile: Profile
    bank_sha256: str
    bank_threshold: Fraction
    threshold: Fraction
    reserved_sha256: str
    reference_ids: tuple[str, ...]
    references_sha256: str
    used: tuple[int, ...]
    selected: BankEntry | None
    log: tuple[ScanStep, ...]
    validation: ValidationResult | None = field(default=None, compare=False, repr=False)
    """The passing `validate()` result of the selected recipe (not serialized)."""

    @property
    def index(self) -> int | None:
        return None if self.selected is None else self.selected.index

    @property
    def exhausted(self) -> bool:
        """True when no bank recipe passed: the whole-book fallback applies."""
        return self.selected is None

    def to_dict(self) -> dict[str, Any]:
        """The scan record (`fallback-scan.schema.json`), logged apart from the 12 slots."""
        sel = self.selected
        return {
            "scan_version": SCAN_VERSION,
            "profile": self.profile.value,
            "bank_sha256": self.bank_sha256,
            "bank_threshold": format_fraction(self.bank_threshold),
            "threshold": format_fraction(self.threshold),
            "reserved_sha256": self.reserved_sha256,
            "validator_version": VALIDATOR_VERSION,
            "renderer_version": RENDERER_VERSION,
            "reference_ids": list(self.reference_ids),
            "references_sha256": self.references_sha256,
            "used": list(self.used),
            "outcome": "exhausted" if sel is None else "selected",
            "selected_index": None if sel is None else sel.index,
            "selected_source": None if sel is None else sel.source,
            "selected_recipe_sha256": None if sel is None else sel.recipe.sha256(),
            "selected_pcm_sha256": None if sel is None else sel.pcm_sha256,
            "log": [s.to_dict() for s in self.log],
        }


def _as_reference(item: Reference | SupportsReference, i: int) -> Reference:
    if isinstance(item, Reference):
        return item
    ref = getattr(item, "reference", None)
    if callable(ref):
        value = ref()
        if isinstance(value, Reference):
            return value
    raise TypeError(f"book_entries[{i}] is {type(item).__name__}, expected a Reference")


def scan_fallback(
    bank: FallbackBank,
    book_entries: Iterable[Reference | SupportsReference],
    *,
    used: Iterable[int] = (),
    threshold: ThresholdLike | None = None,
    reserved: ReservedLike = None,
) -> ScanResult:
    """First unused bank recipe (lowest index) that passes the current book's checks.

    - `book_entries`: the book's committed references in commit order (`Reference`s
      or `StoreEntry`s), all of the bank's profile.
    - `used`: bank indices already assigned in this book; they are skipped and logged
      as `used`.
    - `threshold`: the book's threshold (`None`: `sound/config/validator.json`);
      `reserved=None` loads the registry.

    Each remaining recipe, in index order, goes through `validate()` against the book;
    the scan stops at the first that passes. The log lists every recipe looked at with
    its codes. Pure: it reads its arguments and changes nothing. A bank recipe that no
    longer renders to its recorded waveform raises `FallbackError` (`E_STALE`).
    """
    if not isinstance(bank, FallbackBank):
        raise TypeError(f"bank must be a FallbackBank, got {type(bank).__name__}")
    refs = tuple(_as_reference(item, i) for i, item in enumerate(book_entries))
    skip: set[int] = set()
    for value in used:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < len(bank):
            raise ValueError(f"used bank index {value!r} is not in 0..{len(bank) - 1}")
        skip.add(value)
    limit = _threshold(threshold)
    registry, digest = _reserved(reserved)
    log: list[ScanStep] = []
    selected: BankEntry | None = None
    passing: ValidationResult | None = None
    for entry in bank.entries:
        recipe_sha = entry.recipe.sha256()
        if entry.index in skip:
            log.append(ScanStep(entry.index, recipe_sha, entry.pcm_sha256, "used", (), ()))
            continue
        result = validate(entry.recipe, bank.profile, refs, reserved=registry, threshold=limit)
        if result.pcm_sha256 is not None and result.pcm_sha256 != entry.pcm_sha256:
            raise FallbackError(
                E_STALE,
                f"{entry.source} renders to {result.pcm_sha256}, not its recorded waveform; "
                "rebuild the fallback set with this renderer",
            )
        outcome = "selected" if result.ok else "rejected"
        log.append(
            ScanStep(
                entry.index, recipe_sha, entry.pcm_sha256, outcome, result.codes, result.messages
            )
        )
        if result.ok:
            selected, passing = entry, result
            break
    return ScanResult(
        profile=bank.profile,
        bank_sha256=bank.bank_sha256,
        bank_threshold=bank.threshold,
        threshold=limit,
        reserved_sha256=digest,
        reference_ids=tuple(r.ref_id for r in refs),
        references_sha256=references_digest(refs),
        used=tuple(sorted(skip)),
        selected=selected,
        log=tuple(log),
        validation=passing,
    )


# ---------------------------------------------------------------------------
# Freezing and writing (restricted storage)


@dataclass(frozen=True, slots=True)
class FrozenFallbackBook:
    """A fallback book committed to a store book of kind `fallback` and frozen."""

    profile: Profile
    book_id: str
    chain_head: str
    snapshot_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.value,
            "book_id": self.book_id,
            "chain_head": self.chain_head,
            "snapshot_sha256": self.snapshot_sha256,
        }


def fallback_book_id(fallback: FallbackSet, profile: Profile | str) -> str:
    """Store book ID of a fallback book: `FB-P1-<first 12 hex of fallback_bank_hash>`."""
    return f"FB-{Profile(profile).value}-{fallback.fallback_bank_hash[:12]}"


def freeze_fallback_books(
    store: VocabularyStore, fallback: FallbackSet
) -> tuple[FrozenFallbackBook, ...]:
    """Commit each fallback book to a new store book (`kind="fallback"`, no meanings,
    `source` = `BookAtom.source`, waveform asserted), in stored order, and freeze it.

    The store refuses fallback books inside the repository (`StoreError`, `E_POLICY`) and
    validates every commit again (book threshold = the set's threshold, reserved
    signals). The store's snapshot digest must equal `book_sha256`.
    """
    frozen: list[FrozenFallbackBook] = []
    for book in fallback.books:
        book_id = fallback_book_id(fallback, book.profile)
        store.create_book(book_id, book.profile, kind="fallback", threshold=fallback.threshold)
        for atom in book.atoms:
            store.commit(
                book_id,
                atom.atom_id,
                None,
                atom.recipe,
                source=atom.source,
                profile=book.profile,
                pcm_sha256=atom.pcm_sha256,
            )
        head = store.freeze(book_id)
        digest = snapshot_digest(store.snapshot_hashes(book_id))
        if digest != book.book_sha256:  # pragma: no cover - commit order is the stored order
            raise FallbackError(E_INTEGRITY, f"{book_id}: store snapshot differs from the book")
        frozen.append(FrozenFallbackBook(book.profile, book_id, head, digest))
    return tuple(frozen)


def inside_work_tree(path: str | os.PathLike[str]) -> bool:
    """True when `path` is inside this repository or any git work tree (a directory
    with a `.git` entry above it). Restricted outputs must not go there."""
    target = Path(path).resolve()
    try:
        repo: Path | None = data_root().parent.resolve()
    except FileNotFoundError:  # pragma: no cover - only outside the source tree
        repo = None
    if repo is not None and (target == repo or repo in target.parents):
        return True
    return any((p / ".git").exists() for p in (target, *target.parents))


def _write_json(path: Path, obj: object) -> None:
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    with open(path, "x", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _read_only(path: Path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    os.chmod(path, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def write_fallback(fallback: FallbackSet, out_dir: str | os.PathLike[str]) -> Path:
    """Write the manifest, the build log and every recipe (`.json`) and WAV to `out_dir`.

    Layout: `fallback-manifest.json`, `fallback-build-log.json` (when the set has draw
    logs), `<profile>/bank/<NN>.json|.wav`, `<profile>/book/<atom_id>.json|.wav`. Files
    are created once and made read-only. `out_dir` must be empty or missing
    (`E_EXISTS`). A restricted-seed set is refused inside a git work tree
    (`E_POLICY`). Returns the manifest path.
    """
    out = Path(out_dir)
    if fallback.demo_seed is None and inside_work_tree(out):
        raise FallbackError(
            E_POLICY,
            "fallback banks and books from a restricted seed are study material; write "
            "them to restricted storage outside any git work tree",
        )
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise FallbackError(E_EXISTS, f"{out} exists and is not an empty directory")
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    manifest_path = out / MANIFEST_NAME
    _write_json(manifest_path, fallback.manifest())
    written.append(manifest_path)
    if any(fallback.draw_logs()):
        _write_json(out / BUILD_LOG_NAME, fallback.build_log())
        written.append(out / BUILD_LOG_NAME)
    rows: list[tuple[Path, Profile, Recipe, str, str]] = []
    for bank, book in zip(fallback.banks, fallback.books, strict=True):
        prof = bank.profile
        rows += [
            (
                out / prof.value / "bank" / f"{e.index:02d}",
                prof,
                e.recipe,
                e.pcm_sha256,
                e.file_sha256,
            )
            for e in bank
        ]
        rows += [
            (out / prof.value / "book" / a.atom_id, prof, a.recipe, a.pcm_sha256, a.file_sha256)
            for a in book
        ]
    for stem, prof, recipe, pcm_hash, file_hash in rows:
        stem.parent.mkdir(parents=True, exist_ok=True)
        rendered = render(recipe, prof)
        if rendered.pcm_sha256 != pcm_hash:
            raise FallbackError(E_STALE, f"{stem.name}: the recipe renders to another waveform")
        recipe_path = stem.with_suffix(".json")
        with open(recipe_path, "x", encoding="utf-8", newline="\n") as f:
            f.write(recipe.canonical_json() + "\n")
        wav_path = stem.with_suffix(".wav")
        if write_wav(rendered, wav_path) != file_hash:  # pragma: no cover - same bytes
            raise FallbackError(E_STALE, f"{wav_path.name}: file_sha256 does not match")
        written += [recipe_path, wav_path]
    for path in written:
        _read_only(path)
    return manifest_path
