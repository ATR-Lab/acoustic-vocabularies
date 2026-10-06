"""Frozen reference inputs of a visit (#33). Interface; #33 implements.

Reads, from ``inputs/`` of a data root (paths ``paths.INPUT_PATHS``), everything a visit
is reconciled against: the person's visit schedules (``av-schedules/visit-schedule``;
hidden-answer material), the visit's generated run sheet, the learner-facing slot list
(Study A) or dyad list (Study B, roles needed for C6), the reveal log (coded participant
ID to person slot), the package JSON documents (``av-sound/package`` manifest and
``audio.json``: expected PCM hashes per message and atom, package hash), the
package-hash mapping and, for Study B, the vocabulary-store snapshot after each wave
(``{atom_id: {recipe_sha256, pcm_sha256, profile, semantic_label}}``, sound
``store.md``). These are file contracts with the schedules and sound components; no
module of another stack is imported for them (``av_schedules`` is on this stack and may
be imported).

Logged ``waveform_sha256`` values are PCM-sample hashes (renderer spec D9:
``composite_sha256`` for messages, ``pcm_sha256`` for atoms and Study B options), never
file hashes (**Pending** confirmation by #64 and #72).

Masking: never reads ``keys/``; refuses an ``inputs/`` tree that contains a book key.
DEMO inputs (``demo: true``) are refused in a REAL root.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .paths import DataRoot


@dataclass(frozen=True)
class References:
    """Reference inputs of one visit."""

    study: str
    set_name: str
    unit_id: str
    person_id: str
    visit: str
    schedules: Mapping[str, Mapping[str, Any]]  # visit -> schedule document (all visits)
    run_sheet_rows: tuple[Mapping[str, str], ...]
    package_id: str  # book ID (A) or bank ID (B)
    package_sha256: str
    expected_hashes: Mapping[str, str]  # message or atom ID (B: per combination key) -> PCM SHA-256
    store_snapshots: Mapping[str, Mapping[str, Any]]  # B: visit -> snapshot
    partner_person_id: str | None  # B: the other member of the dyad
    active: bool | None  # B: whether this person is the active member (C6 only)
    inputs: Mapping[str, str]  # every file read (relative path) -> SHA-256


def load_references(root: DataRoot, visit_id: str) -> References:
    """Reference inputs of a visit; raises ``ValueError`` naming the missing input."""
    raise NotImplementedError("#33: reference inputs")


def revealed_persons(root: DataRoot) -> Mapping[str, str]:
    """Person slot -> coded participant ID for every revealed slot (reveal log)."""
    raise NotImplementedError("#33: reveal-log bindings")
