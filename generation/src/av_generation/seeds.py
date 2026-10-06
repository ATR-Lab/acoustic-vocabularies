"""Seed keys and seed derivation (#16, #18, #26; Study A protocol §3.1 and §3.6).

A seed key is a `|`-joined ASCII string whose first part names the seed namespace:

| Namespace | Key | Used by |
| --- | --- | --- |
| `A1` | `A1|<batch_ns>|<atom>|<round>|<slot>` | scripted bot designer (#22) |
| `A2` | `A2|<batch_ns>|<atom>|<round>|<slot>` | A2 mutation search (#18) |
| `A3` | `A3|<batch_ns>|<atom>|<round>|<slot>` | A3 model call (#16, #17) |
| `B` | `B|<bank>|<attempt>|<profile>|<atom>|<slot>` | Study B bank builder (#26) |
| `PANEL` | `PANEL|<set_ns>|<purpose>` | panel order schedule and book-ID rotation (#20) |
| `BOT` | `BOT|<run>|<actor>|<purpose>|...` | bot raters and bot designer (#22) |
| `THRESHOLD` | `THRESHOLD|<set>|<purpose>|...` | listening-tool stimuli and trial order (#23) |

`<batch_ns>` is the batch's seed namespace from the batch config (the batch ID, or a new
namespace when a batch is rebuilt). Rounds, slots and attempts are decimal integers
without leading zeros: `A3|A-P01|K-a1|2|3`. Every part matches `[A-Za-z0-9._-]+`.

`derive_seed(*parts)` is the first 8 bytes of SHA-256 of the UTF-8 key, read as an
unsigned big-endian integer (0 .. 2**64 - 1): identical on every machine. The stored
waveform hash, not the seed, is the reproducibility record (§3.2).

Model servers that validate `seed` as a signed 64-bit integer (vLLM's OpenAI endpoint)
receive `wire_seed(seed)`, the two's-complement reading of the same 8 bytes. Logs keep
both values; the mapping is a bijection.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from enum import StrEnum
from typing import Final, NamedTuple

import numpy as np
from av_sound.grammar import ATOM_IDS

from av_generation.constants import (
    B_MAX_ATTEMPTS,
    B_SLOTS_PER_CELL,
    PROFILES,
    ROUNDS_PER_ATOM,
    SLOTS_PER_ROUND,
)

SEPARATOR: Final = "|"
PART_RE: Final = re.compile(r"[A-Za-z0-9._-]+")
SEED_BITS: Final = 64
SEED_MAX: Final = 2**SEED_BITS - 1


class SeedNamespace(StrEnum):
    A1 = "A1"
    A2 = "A2"
    A3 = "A3"
    B = "B"
    PANEL = "PANEL"
    BOT = "BOT"
    THRESHOLD = "THRESHOLD"


SLOT_NAMESPACES: Final[frozenset[SeedNamespace]] = frozenset(
    {SeedNamespace.A1, SeedNamespace.A2, SeedNamespace.A3, SeedNamespace.B}
)


class SeedKeyError(ValueError):
    """A seed key or one of its parts is malformed."""


class SeedKey(NamedTuple):
    namespace: SeedNamespace
    parts: tuple[str, ...]

    def __str__(self) -> str:
        return SEPARATOR.join((self.namespace.value, *self.parts))


def _part(value: str | int) -> str:
    if isinstance(value, bool):
        raise SeedKeyError(f"seed-key part must be a string or integer, got {value!r}")
    if isinstance(value, int):
        if value < 0:
            raise SeedKeyError(f"seed-key integer parts are non-negative, got {value}")
        return str(value)
    if not isinstance(value, str) or not PART_RE.fullmatch(value):
        raise SeedKeyError(f"seed-key part {value!r} must match {PART_RE.pattern}")
    return value


def join_key(*parts: str | int) -> str:
    """The canonical key for `parts` (integers in decimal). The first part is a namespace."""
    if not parts:
        raise SeedKeyError("a seed key needs at least a namespace")
    text = [_part(p) for p in parts]
    if text[0] not in SeedNamespace.__members__:
        raise SeedKeyError(f"unknown seed namespace {text[0]!r}")
    return SEPARATOR.join(text)


def seed_from_key(key: str) -> int:
    """First 8 bytes of SHA-256(key) as an unsigned big-endian integer."""
    if not isinstance(key, str) or not key:
        raise SeedKeyError("seed key must be a non-empty string")
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def derive_seed(*parts: str | int) -> int:
    """`seed_from_key(join_key(*parts))`; e.g. `derive_seed("A3", "A-P01", "K-a1", 2, 3)`."""
    return seed_from_key(join_key(*parts))


def wire_seed(seed: int) -> int:
    """The signed 64-bit value sent to an OpenAI-compatible server for `seed`."""
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= SEED_MAX:
        raise SeedKeyError(f"seed must be an integer 0..2**64-1, got {seed!r}")
    return seed - 2**SEED_BITS if seed >= 2 ** (SEED_BITS - 1) else seed


def unwire_seed(value: int) -> int:
    """Inverse of `wire_seed`."""
    if isinstance(value, bool) or not -(2 ** (SEED_BITS - 1)) <= value < 2 ** (SEED_BITS - 1):
        raise SeedKeyError(f"wire seed must be a signed 64-bit integer, got {value!r}")
    return value + 2**SEED_BITS if value < 0 else value


def _check_int(name: str, value: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise SeedKeyError(f"{name} must be an integer {low}..{high}, got {value!r}")
    return value


def _study_a_key(ns: SeedNamespace, batch_ns: str, atom_id: str, round_: int, slot: int) -> str:
    if atom_id not in ATOM_IDS:
        raise SeedKeyError(f"not an atom ID: {atom_id!r}")
    _check_int("round", round_, 1, ROUNDS_PER_ATOM)
    _check_int("slot", slot, 1, SLOTS_PER_ROUND)
    return join_key(ns.value, batch_ns, atom_id, round_, slot)


def a1_seed_key(batch_ns: str, atom_id: str, round_: int, slot: int) -> str:
    """`A1|batch|atom|round|slot` (only scripted designers draw random numbers)."""
    return _study_a_key(SeedNamespace.A1, batch_ns, atom_id, round_, slot)


def a2_seed_key(batch_ns: str, atom_id: str, round_: int, slot: int) -> str:
    """`A2|batch|atom|round|slot` (#18: one PCG64 stream per slot)."""
    return _study_a_key(SeedNamespace.A2, batch_ns, atom_id, round_, slot)


def a3_seed_key(batch_ns: str, atom_id: str, round_: int, slot: int) -> str:
    """`A3|batch|atom|round|slot` (#16: the model call's seed)."""
    return _study_a_key(SeedNamespace.A3, batch_ns, atom_id, round_, slot)


def b_seed_key(bank_id: str, attempt: int, profile: str, atom_id: str, slot: int) -> str:
    """`B|bank|attempt|profile|atom|slot` (#26: slot 1..12 within the cell)."""
    _check_int("attempt", attempt, 1, B_MAX_ATTEMPTS)
    if profile not in PROFILES:
        raise SeedKeyError(f"not a profile: {profile!r}")
    if atom_id not in ATOM_IDS:
        raise SeedKeyError(f"not an atom ID: {atom_id!r}")
    _check_int("slot", slot, 1, B_SLOTS_PER_CELL)
    return join_key(SeedNamespace.B.value, bank_id, attempt, profile, atom_id, slot)


def panel_seed_key(set_ns: str, purpose: str) -> str:
    """`PANEL|set_ns|purpose`, e.g. `PANEL|A-C|orders` (#20)."""
    return join_key(SeedNamespace.PANEL.value, set_ns, purpose)


def bot_seed_key(run_id: str, actor: str, purpose: str, *parts: str | int) -> str:
    """`BOT|run|actor|purpose|...`, e.g. `BOT|DEMO-dry-1|R2|rating|A-P01.K-a1.r1p3` (#22)."""
    return join_key(SeedNamespace.BOT.value, run_id, actor, purpose, *parts)


def threshold_seed_key(set_id: str, purpose: str, *parts: str | int) -> str:
    """`THRESHOLD|set|purpose|...`, e.g. `THRESHOLD|DEMO-T1|pair|P1|3|7` (#23)."""
    return join_key(SeedNamespace.THRESHOLD.value, set_id, purpose, *parts)


def parse_seed_key(key: str) -> SeedKey:
    """Split and check a key; raises `SeedKeyError`. Slot namespaces are checked fully."""
    if not isinstance(key, str):
        raise SeedKeyError(f"seed key must be a string, got {key!r}")
    parts = key.split(SEPARATOR)
    join_key(*parts)  # checks the namespace and every part
    ns = SeedNamespace(parts[0])
    rest = tuple(parts[1:])
    try:
        if ns in (SeedNamespace.A1, SeedNamespace.A2, SeedNamespace.A3):
            if len(rest) != 4:
                raise SeedKeyError(f"{ns} keys have 4 parts after the namespace: {key!r}")
            canonical = _study_a_key(ns, rest[0], rest[1], _int(rest[2]), _int(rest[3]))
        elif ns is SeedNamespace.B:
            if len(rest) != 5:
                raise SeedKeyError(f"B keys have 5 parts after the namespace: {key!r}")
            canonical = b_seed_key(rest[0], _int(rest[1]), rest[2], rest[3], _int(rest[4]))
        else:
            canonical = key
    except ValueError as err:
        raise SeedKeyError(f"malformed seed key {key!r}: {err}") from err
    if canonical != key:
        raise SeedKeyError(f"not a canonical seed key: {key!r} (expected {canonical!r})")
    return SeedKey(ns, rest)


def _int(text: str) -> int:
    if not text.isdigit() or (len(text) > 1 and text[0] == "0"):
        raise SeedKeyError(f"integer parts are decimal without leading zeros, got {text!r}")
    return int(text)


def rng_for(key: str) -> np.random.Generator:
    """NumPy `Generator(PCG64(seed_from_key(key)))`: the portable stream for a seed key."""
    return np.random.Generator(np.random.PCG64(seed_from_key(key)))


def seeds_digest(keys: Sequence[str]) -> str:
    """SHA-256 over `key=seed` lines (one per key, `\\n`-terminated): a cross-machine check."""
    lines = "".join(f"{k}={seed_from_key(k)}\n" for k in keys)
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()
