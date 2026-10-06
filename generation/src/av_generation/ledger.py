"""Shared slot ledger for A1, A2, A3 and Study B (#17). INTERFACE ONLY in the skeleton.

One slot = one submitted recipe. Every outcome code consumes the slot; nothing is
replenished, repaired or retried. The ledger is an append-only JSONL file of
`records.SlotRecord` (`rundir.LOG_FILES["slot"]`), and refusals go to
`records.SlotRefusal` (`LOG_FILES["slot_refusal"]`).

Two steps per slot, so the cap is checked before any work is done:

1. `reserve(cap_key, slot_id, study=, method=)` checks the cap and the slot ID and returns
   a `SlotTicket` (with the slot's `slot_index` and open time). A 13th slot raises
   `SlotCapExceeded` and a used or open slot ID raises `SlotReused`, each after logging a
   `SlotRefusal`; nothing is charged. A3 and B reserve before `count_prompt_tokens` and
   `propose` (no model call beyond the cap; Study B protocol §4), A2 before sampling, and
   A1 when the designer opens a slot (#19: "a 13th slot request is refused").
2. `consume(record)` closes the ticket: `record.slot_id`, `cap_key` and `slot_index` must
   match an open ticket (else `SlotNotReserved`), then the record is appended atomically.
   Every reserved slot is consumed with some outcome (`timeout` at the latest).

Caps (`records.cap_key`): 12 slots per book and atom (Study A: per atom per method, since
a batch has one book per method) and 12 per bank attempt and cell (Study B). Study B
attempts (at most 4) are counted by the bank builder (#26) with `AttemptCapExceeded`.

Reopening: the constructor calls `jsonio.repair_torn_tail` on both files (logging a
`log_repaired` timing event through `timing`, if given), then counts the existing
records toward the caps, so a resumed batch continues where it stopped. Tickets live in
memory: a slot reserved but not consumed when the process stopped has no record and may
be reserved again after the restart (a logged deviation, not a second charge).

Thread safety: proposers of different methods reserve and consume concurrently (the
orchestrator runs the proposal windows in parallel), so both calls are atomic per ledger.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from av_generation.clock import Clock
from av_generation.constants import SLOTS_PER_ATOM
from av_generation.ids import Method, Study
from av_generation.records import RecordWriter, SlotRecord


class LedgerError(RuntimeError):
    """Base class; `.code` names the rule."""

    code = "E_LEDGER"


class SlotCapExceeded(LedgerError):
    """A 13th slot was requested for one cap key."""

    code = "E_SLOT_CAP"


class SlotReused(LedgerError):
    """A slot ID was reserved or consumed twice."""

    code = "E_SLOT_REUSED"


class SlotNotReserved(LedgerError):
    """`consume` got a record without a matching open ticket."""

    code = "E_NOT_RESERVED"


class AttemptCapExceeded(LedgerError):
    """A 5th Study B bank attempt was requested."""

    code = "E_ATTEMPT_CAP"


@dataclass(frozen=True, slots=True)
class SlotTicket:
    """An open slot: reserved under the cap, not yet consumed."""

    cap_key: str
    slot_id: str
    slot_index: int
    """1..12: this slot's submission number within the cap key (the tie rule's order)."""
    study: Study
    method: Method
    t_open_ms: int


class SlotLedger:
    """Append-only slot ledger with hard caps (#17 implements every method)."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        run_id: str,
        clock: Clock,
        refusals: RecordWriter | None = None,
        timing: RecordWriter | None = None,
        cap: int = SLOTS_PER_ATOM,
    ) -> None:
        """Open (or create) the ledger at `path` (see the module docstring)."""
        raise NotImplementedError("#17: slot ledger")

    def reserve(self, cap_key: str, slot_id: str, *, study: Study, method: Method) -> SlotTicket:
        """Open one slot under the cap; raises `SlotCapExceeded` / `SlotReused` (logged)."""
        raise NotImplementedError("#17: slot ledger")

    def consume(self, record: SlotRecord) -> SlotRecord:
        """Close an open ticket with its record and append it atomically."""
        raise NotImplementedError("#17: slot ledger")

    def used(self, cap_key: str) -> int:
        """Slots consumed or open under `cap_key`."""
        raise NotImplementedError("#17: slot ledger")

    def remaining(self, cap_key: str) -> int:
        """`cap - used(cap_key)`."""
        raise NotImplementedError("#17: slot ledger")

    def records(self, cap_key: str | None = None) -> tuple[SlotRecord, ...]:
        """Records in ledger order (all, or one cap key's)."""
        raise NotImplementedError("#17: slot ledger")
