"""Seed handling: master seeds, SHA-256 seed derivation and a portable random stream.

* A master seed is either a private value read from a file (never committed) or a
  public demonstration value that must start with ``DEMO-``.
* Derived seeds are ``sha256("{master}|{study}|{unit}|{purpose}")`` as lowercase hex.
  ``unit`` is a unit ID (``A-C01``) or a design token (``A-C-cyc01``, ``B-C-blk03``).
* :class:`SeedStream` turns a derived seed into integers without ``random``'s Mersenne
  Twister, so the procedure is specified completely here and can be re-implemented in
  any language: draw ``k`` is the first 8 bytes (big-endian) of
  ``sha256("{seed}#{k}")``; ``randbelow(n)`` rejects draws ``>= floor(2**64 / n) * n``.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeVar

T = TypeVar("T")

DEMO_PREFIX: Final = "DEMO-"
_DEMO_RE: Final = re.compile(r"DEMO-[A-Za-z0-9._-]{1,64}")
_MASTER_RE: Final = re.compile(r"[A-Za-z0-9._-]{32,256}")
_TOKEN_RE: Final = re.compile(r"[A-Za-z0-9._:-]+")
_LIMIT: Final = 1 << 64


@dataclass(frozen=True)
class MasterSeed:
    """A master seed. ``fingerprint`` is safe to publish; ``value`` is not (unless demo)."""

    value: str
    demo: bool

    def __repr__(self) -> str:  # never print a private seed by accident
        shown = self.value if self.demo else "<private>"
        return f"MasterSeed({shown!r}, demo={self.demo})"

    @property
    def fingerprint(self) -> str:
        """SHA-256 of the master seed value (lowercase hex)."""
        return hashlib.sha256(self.value.encode("utf-8")).hexdigest()

    @property
    def label(self) -> str:
        """Public label: the demo seed itself, or ``sha256:<fingerprint>``."""
        return self.value if self.demo else f"sha256:{self.fingerprint}"


def demo_seed(value: str) -> MasterSeed:
    """A public demonstration seed; must match ``DEMO-[A-Za-z0-9._-]{1,64}``."""
    if not _DEMO_RE.fullmatch(value):
        raise ValueError("demo seeds must start with 'DEMO-' followed by 1-64 of [A-Za-z0-9._-]")
    return MasterSeed(value, demo=True)


def private_seed(value: str) -> MasterSeed:
    """A private master seed: 32-256 characters of ``[A-Za-z0-9._-]``, not ``DEMO-``."""
    value = value.strip()
    if value.startswith(DEMO_PREFIX):
        raise ValueError("a private master seed must not start with 'DEMO-'")
    if not _MASTER_RE.fullmatch(value):
        raise ValueError(
            "a private master seed must be 32-256 characters of [A-Za-z0-9._-] "
            "(e.g. 64 hex digits from secrets.token_hex(32))"
        )
    return MasterSeed(value, demo=False)


def load_master_seed(path: Path) -> MasterSeed:
    """Read a private master seed from a UTF-8 text file (surrounding whitespace ignored)."""
    return private_seed(path.read_text(encoding="utf-8"))


def derive_seed(master: MasterSeed, study: str, unit: str, purpose: str) -> str:
    """``sha256("{master}|{study}|{unit}|{purpose}")`` as 64 lowercase hex digits."""
    for name, part in (("study", study), ("unit", unit), ("purpose", purpose)):
        if not _TOKEN_RE.fullmatch(part):
            raise ValueError(f"{name} token {part!r} must match [A-Za-z0-9._:-]+")
    text = f"{master.value}|{study}|{unit}|{purpose}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SeedStream:
    """Deterministic integer stream from a hex seed (see module docstring)."""

    def __init__(self, seed: str) -> None:
        self._seed = seed
        self._k = 0

    def _draw(self) -> int:
        digest = hashlib.sha256(f"{self._seed}#{self._k}".encode()).digest()
        self._k += 1
        return int.from_bytes(digest[:8], "big")

    def randbelow(self, n: int) -> int:
        """Uniform integer in ``[0, n)`` by rejection sampling."""
        if n <= 0:
            raise ValueError("n must be positive")
        limit = (_LIMIT // n) * n
        while True:
            x = self._draw()
            if x < limit:
                return x % n

    def permutation(self, items: Sequence[T]) -> tuple[T, ...]:
        """Fisher-Yates shuffle (i from n-1 down to 1, j = randbelow(i + 1))."""
        out = list(items)
        for i in range(len(out) - 1, 0, -1):
            j = self.randbelow(i + 1)
            out[i], out[j] = out[j], out[i]
        return tuple(out)


def stream(master: MasterSeed, study: str, unit: str, purpose: str) -> SeedStream:
    """Shorthand for ``SeedStream(derive_seed(master, study, unit, purpose))``."""
    return SeedStream(derive_seed(master, study, unit, purpose))
