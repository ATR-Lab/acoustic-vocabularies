"""Frozen reference inputs of a visit (#33). Interface; #33 implements.

Reads, from ``inputs/`` of a data root (paths ``paths.INPUT_PATHS``), everything a visit
is reconciled against: the person's visit schedules (``av-schedules/visit-schedule``;
hidden-answer material), the visit's generated run sheet, the learner-facing slot list
(Study A) or dyad list (Study B, roles needed for C6), the reveal log (coded participant
ID to person slot), the package JSON documents (``av-sound/package`` manifest and
``audio.json``, package hash), the package-hash mapping and, for Study B, the store
snapshots and selection receipts of the dyad's book. These are file contracts with the
schedules, sound and apparatus components; no module of another stack is imported for
them (``av_schedules`` is on this stack and may be imported).

**Expected waveform hashes (C3).** ``audio.json`` gives two hashes per playable item: the
PCM-sample hash (``pcm_sha256`` of atoms and Study B options, ``composite_sha256`` of
messages) and the WAV file hash (``file_sha256``; ``null`` for audio that exists only
when composed in memory: held-out messages and all Study B messages). A logged
``waveform_sha256`` is the file hash for file playback and the PCM hash for composed
audio (``vocab``, **Pending** #64/#72): C3 accepts a logged hash equal to either hash of
the scheduled item (:class:`ExpectedHash`), reports ``WAVEFORM_HASH_MISMATCH`` when it
equals neither, and ``WAVEFORM_HASH_MISSING`` when it is empty, except for composed audio
whose PCM hash the export carries in the ``pcm_sha256`` extension column.

**Store snapshots (C5, Study B).** ``inputs/store-snapshots/{unit_id}/{visit}.json`` is the
menu-store bridge's ``verified_snapshot`` result after the visit's selections
(``docs/interfaces/menu-store-bridge.md``, #70): ``book_head``, ``snapshot_sha256``,
``journal_head``, ``profile``, ``profile_selection_receipt_sha256`` and ``entries``
(``atom_id``, ``profile``, ``rank``, ``pcm_sha256``, ``file_sha256``,
``selection_receipt_sha256``); ``receipts.jsonl`` holds the bridge's selection receipts in
order. C5: every atom of an earlier wave keeps its entry (profile, rank, PCM and file hash,
selection receipt) in every later snapshot, the profile and its selection receipt never
change, and the receipts' ``before_head``/``after_head`` chain links each snapshot's
``book_head`` to the next. The bridge returns no recipe or semantic label, so per-atom
recipe identity is covered only through ``snapshot_sha256`` and the head chain:
**Pending** (#70, #26) a store-verification artifact that lists recipe hashes per atom.
A REAL root refuses a snapshot whose ``source_kind`` is ``synthetic``.

Masking: never reads ``keys/``; refuses an ``inputs/`` tree that contains a book key.
DEMO inputs (``demo: true``) are refused in a REAL root. The Study B roles read from the
dyad list are used only inside C6 and never written to any output (``reconcile``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .paths import DataRoot


@dataclass(frozen=True)
class ExpectedHash:
    """The two hashes a played item may be logged with (``audio.json``)."""

    item_key: str  # message or atom ID (A); option or message combination key (B)
    pcm_sha256: str  # PCM-sample hash: composite_sha256 (messages) or pcm_sha256
    file_sha256: str | None  # WAV file hash; None when the audio exists only when composed

    def matches(self, logged: str) -> bool:
        """True if a logged ``waveform_sha256`` equals either hash of the item."""
        return logged == self.pcm_sha256 or (
            self.file_sha256 is not None and logged == self.file_sha256
        )


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
    # Study A: message or atom ID. Study B: ``<atom_id>/<profile>/<rank>`` for options and
    # ``<message_id>/<profile>/<action_rank>/<referent_rank>`` for message combinations.
    expected_hashes: Mapping[str, ExpectedHash]
    store_snapshots: Mapping[str, Mapping[str, Any]]  # B: visit -> verified_snapshot
    store_receipts: tuple[Mapping[str, Any], ...]  # B: bridge selection receipts, in order
    partner_person_id: str | None  # B: the other member of the dyad
    active_person_id: str | None  # B: the dyad's active member; C6 only, never written
    inputs: Mapping[str, str]  # every file read (relative path) -> SHA-256


def load_references(root: DataRoot, visit_id: str) -> References:
    """Reference inputs of a visit; raises ``ValueError`` naming the missing input."""
    raise NotImplementedError("#33: reference inputs")


def revealed_persons(root: DataRoot) -> Mapping[str, str]:
    """Person slot -> coded participant ID for every revealed slot (reveal log)."""
    raise NotImplementedError("#33: reveal-log bindings")
