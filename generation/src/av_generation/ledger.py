"""Shared slot ledger for A1, A2, A3 and Study B (#17). INTERFACE ONLY in the skeleton.

One slot = one submitted recipe. Every outcome code consumes the slot; nothing is
replenished, repaired or retried. The ledger is an append-only JSONL file of
`records.SlotRecord` (`rundir.LOG_FILES["slot"]`), and refusals go to
`records.SlotRefusal` (`LOG_FILES["slot_refusal"]`).

Caps (`records.cap_key`): 12 slots per book and atom (Study A: per atom per method, since
a batch has one book per method) and 12 per bank attempt and cell (Study B). A 13th slot
raises `SlotCapExceeded` after logging a refusal. Consuming a slot ID twice raises
`SlotReused` (logged). Study B attempts (at most 4) are counted by the bank builder (#26)
with `AttemptCapExceeded`.

Thread safety: proposers of different methods consume concurrently (the orchestrator runs
the proposal windows in parallel), so `consume` must be atomic per ledger file.
"""

from __future__ import annotations

import os

from av_generation.clock import Clock
from av_generation.constants import SLOTS_PER_ATOM
from av_generation.records import RecordWriter, SlotRecord


class LedgerError(RuntimeError):
    """Base class; `.code` names the rule."""

    code = "E_LEDGER"


class SlotCapExceeded(LedgerError):
    """A 13th slot was requested for one cap key."""

    code = "E_SLOT_CAP"


class SlotReused(LedgerError):
    """A slot ID was consumed twice."""

    code = "E_SLOT_REUSED"


class AttemptCapExceeded(LedgerError):
    """A 5th Study B bank attempt was requested."""

    code = "E_ATTEMPT_CAP"


class SlotLedger:
    """Append-only slot ledger with hard caps (#17 implements every method)."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        run_id: str,
        clock: Clock,
        refusals: RecordWriter | None = None,
        cap: int = SLOTS_PER_ATOM,
    ) -> None:
        """Open (or create) the ledger at `path`; existing records count toward the caps,
        so a resumed batch continues where it stopped."""
        raise NotImplementedError("#17: slot ledger")

    def consume(self, record: SlotRecord) -> SlotRecord:
        """Charge one slot: check the cap and slot ID, then append `record` atomically.

        Raises `SlotCapExceeded` / `SlotReused` (after appending a `SlotRefusal`).
        """
        raise NotImplementedError("#17: slot ledger")

    def used(self, cap_key: str) -> int:
        """Slots consumed under `cap_key`."""
        raise NotImplementedError("#17: slot ledger")

    def remaining(self, cap_key: str) -> int:
        """`cap - used(cap_key)`."""
        raise NotImplementedError("#17: slot ledger")

    def records(self, cap_key: str | None = None) -> tuple[SlotRecord, ...]:
        """Records in ledger order (all, or one cap key's)."""
        raise NotImplementedError("#17: slot ledger")
