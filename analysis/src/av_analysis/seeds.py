"""Stored seeds for every random draw of the analysis stack (bootstraps, simulations,
synthetic logs).

A seed is a label string that is written into the output that used it (``seed`` fields of
reports, logs and manifests), so a rerun with the same label gives identical bytes. The
generator is ``numpy.random.Generator(PCG64(n))`` with ``n`` = the first 16 bytes,
big-endian, of ``sha256("|".join(parts))``. Synthetic and demonstration seeds start with
``DEMO-`` (as in ``av_schedules.seeds``); seeds of real analyses are labels such as
``A-primary-bootstrap-v1`` that are published with the report. No wall clock, no global
random state.
"""

from __future__ import annotations

import hashlib
import re
from typing import Final

import numpy as np

_PART_RE: Final = re.compile(r"[A-Za-z0-9._:-]+")


def seed_int(*parts: str) -> int:
    """128-bit integer seed from label parts (each matching ``[A-Za-z0-9._:-]+``)."""
    if not parts:
        raise ValueError("at least one seed part is required")
    for part in parts:
        if not _PART_RE.fullmatch(part):
            raise ValueError(f"invalid seed part {part!r}: use [A-Za-z0-9._:-]+")
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:16], "big")


def rng(*parts: str) -> np.random.Generator:
    """A PCG64 generator seeded from label parts."""
    return np.random.Generator(np.random.PCG64(seed_int(*parts)))


def is_demo(label: str) -> bool:
    """True for a demonstration or synthetic seed label (``DEMO-...``)."""
    return label.startswith("DEMO-")
