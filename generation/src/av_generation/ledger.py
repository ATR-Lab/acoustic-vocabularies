"""Shared slot ledger for A1, A2, A3 and Study B (#17; Study A protocol §3.3, Study B §4).

One slot = one submitted recipe. Every outcome code consumes the slot; nothing is
replenished, repaired or retried. The ledger is an append-only JSONL file of
`records.SlotRecord` (`rundir.LOG_FILES["slot"]`), and refusals go to
`records.SlotRefusal` (`LOG_FILES["slot_refusal"]`).

Two steps per slot, so the cap is checked before any work is done:

1. `reserve(cap_key, slot_id, study=, method=)` checks the cap and the slot ID and returns
   a `SlotTicket` (with the slot's `slot_index` and open time). A 13th slot raises
   `SlotCapExceeded` (refusal reason `slot_cap`), and a slot ID that is open raises
   `SlotReused` (`slot_reused`) or that was consumed raises `SlotReused` (`slot_closed`: a
   slot never reopens), each after logging a `SlotRefusal`; nothing is charged. The cap is
   checked first, so any request beyond the cap is refused as `slot_cap`. A3 and B reserve
   before `count_prompt_tokens` and `propose` (no model call beyond the cap; Study B
   protocol §4), A2 before sampling, and A1 when a slot opens (#19: slot 1 at the
   round start, each next one when the previous closes; "a 13th slot request is
   refused").
2. `consume(record)` closes the ticket: `record.slot_id`, `cap_key`, `slot_index`,
   `study`, `method`, `run_id` and `t_open_ms` must match an open ticket, and the slot ID
   must name the record's book (or bank, attempt and profile), atom, round and slot (else
   `SlotNotReserved`; a record for an already consumed slot also logs a `slot_closed`
   refusal). Then the record is validated and appended as one canonical line. A record
   that fails its schema raises `records.RecordError` and leaves the ticket open. Every
   reserved slot is consumed with some outcome (`timeout` at the latest).

Caps (`records.cap_key`): 12 slots per book and atom (Study A: per atom per method, since
a batch has one book per method; one cap key never mixes methods) and 12 per bank attempt
and cell (Study B). `slot_index` is the submission-slot number of the tie rule:
`ids.slot_index(round, slot)` in Study A and the cell slot (1..12) in Study B. Study B
attempts (at most 4) are counted by the bank builder (#26), which calls
`check_attempt(bank_id, attempt)`: a 5th attempt raises `AttemptCapExceeded` after logging
an `attempt_cap` refusal (cap key `B|<bank>`).

Reopening: the constructor calls `jsonio.repair_torn_tail` on the ledger and the refusal
log (logging a `log_repaired` timing event per cut, through `timing` or the run's
`timing.jsonl` next to the ledger), then reads the existing records (they must belong to
`run_id`, be unique per slot ID and stay within the cap) and counts them toward the caps,
so a resumed batch continues where it stopped. Tickets live in memory: a slot reserved but
not consumed when the process stopped has no record and may be reserved again after the
restart (a logged deviation, not a second charge).

Refusals are always logged: to `refusals` if given, else to `slot-refusals.jsonl` next to
the ledger (created on the first refusal).

Thread safety: proposers of different methods reserve and consume concurrently (the
orchestrator runs the proposal windows in parallel), so both calls are atomic per ledger.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, Literal

from av_generation.clock import Clock
from av_generation.constants import B_MAX_ATTEMPTS, SLOTS_PER_ATOM
from av_generation.ids import (
    STUDY_A_METHODS,
    IdError,
    Method,
    Study,
    check_id,
    parse_bank_slot_id,
    parse_proposal_slot_id,
    slot_index,
)
from av_generation.jsonio import TornTail, repair_torn_tail
from av_generation.records import (
    RecordError,
    RecordWriter,
    SlotRecord,
    SlotRefusal,
    TimingEvent,
    cap_key,
    read_records,
)
from av_generation.rundir import LOG_FILES

REFUSALS_NAME: Final = PurePosixPath(LOG_FILES["slot_refusal"]).name
TIMING_NAME: Final = PurePosixPath(LOG_FILES["timing"]).name
RefusalReason = Literal["slot_cap", "attempt_cap", "slot_reused", "slot_closed"]


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


@dataclass(frozen=True, slots=True)
class _SlotName:
    """What a slot ID says: its cap key, slot index and record fields."""

    cap_key: str
    slot_index: int
    fields: dict[str, object]


def _slot_name(slot_id: str, study: Study, method: Method) -> _SlotName:
    try:
        if study is Study.A:
            if method not in STUDY_A_METHODS:
                raise LedgerError(f"method {method} is not a Study A method")
            a = parse_proposal_slot_id(slot_id)
            return _SlotName(
                cap_key(Study.A, a.atom_id, book_id=a.book_id),
                slot_index(a.round, a.slot),
                {"book_id": a.book_id, "atom_id": a.atom_id, "round": a.round, "slot": a.slot},
            )
        if method is not Method.B:
            raise LedgerError(f"method {method} is not the Study B method")
        b = parse_bank_slot_id(slot_id)
        return _SlotName(
            cap_key(Study.B, b.atom_id, bank_id=b.bank_id, attempt=b.attempt, profile=b.profile),
            b.slot,
            {
                "bank_id": b.bank_id,
                "attempt": b.attempt,
                "profile": b.profile,
                "atom_id": b.atom_id,
                "slot": b.slot,
            },
        )
    except IdError as err:
        raise LedgerError(f"slot ID {slot_id!r}: {err}") from None


def _record_cap_key(record: SlotRecord) -> str | None:
    try:
        return record.cap_key
    except (RecordError, AttributeError, ValueError):
        return None


class SlotLedger:
    """Append-only slot ledger with hard caps (see the module docstring)."""

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
        if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
            raise ValueError(f"cap must be a positive integer, got {cap!r}")
        self.path = Path(path)
        self.run_id = check_id(run_id, "run ID")
        self.cap = cap
        self._clock = clock
        self._lock = threading.RLock()
        self._refusals = refusals
        self._timing = timing
        refusal_path = refusals.path if refusals is not None else self.path.parent / REFUSALS_NAME
        self._refusal_path = refusal_path
        self.repairs: tuple[TornTail, ...] = tuple(
            tail for tail in (repair_torn_tail(p) for p in (self.path, refusal_path)) if tail
        )
        for tail in self.repairs:
            self._log_repair(tail)
        self._records: list[SlotRecord] = []
        self._consumed: dict[str, int] = {}
        self._closed: set[str] = set()
        self._open: dict[str, SlotTicket] = {}
        self._owner: dict[str, Method] = {}
        if self.path.exists():
            for record in read_records(self.path, SlotRecord):
                self._load(record)
        self._writer = RecordWriter(self.path, types=(SlotRecord,))

    # -- logging ---------------------------------------------------------------

    def _log_repair(self, tail: TornTail) -> None:
        if self._timing is None:
            self._timing = RecordWriter(self.path.parent / TIMING_NAME, types=(TimingEvent,))
        self._timing.append(
            TimingEvent(
                run_id=self.run_id,
                event="log_repaired",
                t_ms=self._clock.now_ms(),
                component="ledger",
                detail=(
                    f"{tail.name}: cut {tail.n_bytes} bytes of a torn last line at offset "
                    f"{tail.offset} (sha256 {tail.sha256})"
                ),
            )
        )

    def _refuse(
        self,
        study: Study,
        method: Method,
        key: str,
        reason: RefusalReason,
        requested: str,
        used: int,
        detail: str,
    ) -> None:
        if self._refusals is None:
            self._refusals = RecordWriter(self._refusal_path, types=(SlotRefusal,))
        self._refusals.append(
            SlotRefusal(
                run_id=self.run_id,
                study=study,
                method=method,
                cap_key=key,
                reason=reason,
                requested=requested,
                used=used,
                t_ms=self._clock.now_ms(),
                detail=detail,
            )
        )

    # -- state -----------------------------------------------------------------

    def _load(self, record: SlotRecord) -> None:
        if record.run_id != self.run_id:
            raise LedgerError(f"{self.path}: record of run {record.run_id}, expected {self.run_id}")
        if record.slot_id in self._closed:
            raise LedgerError(f"{self.path}: slot {record.slot_id} appears twice")
        key = record.cap_key
        owner = self._owner.setdefault(key, record.method)
        if owner is not record.method:
            raise LedgerError(
                f"{self.path}: cap key {key} mixes methods {owner} and {record.method}"
            )
        count = self._consumed.get(key, 0) + 1
        if count > self.cap:
            raise LedgerError(f"{self.path}: {count} slots under {key}, cap {self.cap}")
        self._consumed[key] = count
        self._closed.add(record.slot_id)
        self._records.append(record)

    def _used(self, key: str) -> int:
        return self._consumed.get(key, 0) + sum(1 for t in self._open.values() if t.cap_key == key)

    # -- public API --------------------------------------------------------------

    def reserve(self, cap_key: str, slot_id: str, *, study: Study, method: Method) -> SlotTicket:
        """Open one slot under the cap; raises `SlotCapExceeded` / `SlotReused` (logged).

        Raises `LedgerError` (not logged) when the slot ID does not belong to `cap_key`,
        the method does not fit the study, or the cap key belongs to another method.
        """
        study, method = Study(study), Method(method)
        name = _slot_name(slot_id, study, method)
        if name.cap_key != cap_key:
            raise LedgerError(f"slot {slot_id} belongs to cap key {name.cap_key}, not {cap_key}")
        with self._lock:
            used = self._used(cap_key)
            if used >= self.cap:
                self._refuse(
                    study, method, cap_key, "slot_cap", slot_id, used, f"cap of {self.cap} reached"
                )
                raise SlotCapExceeded(f"{cap_key}: all {self.cap} slots used; {slot_id} refused")
            if slot_id in self._open:
                self._refuse(study, method, cap_key, "slot_reused", slot_id, used, "slot is open")
                raise SlotReused(f"{slot_id} is already reserved")
            if slot_id in self._closed:
                self._refuse(study, method, cap_key, "slot_closed", slot_id, used, "slot closed")
                raise SlotReused(f"{slot_id} was consumed; a slot never reopens")
            owner = self._owner.get(cap_key)
            if owner is not None and owner is not method:
                raise LedgerError(f"cap key {cap_key} belongs to {owner}, not {method}")
            ticket = SlotTicket(
                cap_key, slot_id, name.slot_index, study, method, self._clock.now_ms()
            )
            self._owner.setdefault(cap_key, method)
            self._open[slot_id] = ticket
            return ticket

    def _mismatches(self, ticket: SlotTicket, record: SlotRecord) -> list[str]:
        problems = []
        key = _record_cap_key(record)
        if key is None:
            return ["the record has no valid cap key (book, or bank, attempt and profile)"]
        expected = {
            "run_id": self.run_id,
            "cap_key": ticket.cap_key,
            "slot_index": ticket.slot_index,
            "study": ticket.study,
            "method": ticket.method,
            "t_open_ms": ticket.t_open_ms,
        }
        actual = {
            "run_id": record.run_id,
            "cap_key": key,
            "slot_index": record.slot_index,
            "study": record.study,
            "method": record.method,
            "t_open_ms": record.t_open_ms,
        }
        problems += [
            f"{k}={actual[k]!r} (expected {v!r})" for k, v in expected.items() if actual[k] != v
        ]
        named = _slot_name(ticket.slot_id, ticket.study, ticket.method).fields
        problems += [
            f"{k}={getattr(record, k)!r} (slot ID says {v!r})"
            for k, v in named.items()
            if getattr(record, k) != v
        ]
        if record.t_ms < record.t_open_ms:
            problems.append("t_ms is before t_open_ms")
        return problems

    def consume(self, record: SlotRecord) -> SlotRecord:
        """Close an open ticket with its record and append it atomically."""
        if not isinstance(record, SlotRecord):
            raise TypeError(f"consume() takes a SlotRecord, got {type(record).__name__}")
        with self._lock:
            ticket = self._open.get(record.slot_id)
            if ticket is None:
                if record.slot_id in self._closed:
                    key = _record_cap_key(record)
                    if key is not None:
                        self._refuse(
                            record.study,
                            record.method,
                            key,
                            "slot_closed",
                            record.slot_id,
                            self._used(key),
                            "second record for a consumed slot",
                        )
                raise SlotNotReserved(f"{record.slot_id} has no open ticket")
            problems = self._mismatches(ticket, record)
            if problems:
                raise SlotNotReserved(
                    f"{record.slot_id}: record does not match its ticket: {'; '.join(problems)}"
                )
            self._writer.append(record)
            del self._open[record.slot_id]
            self._closed.add(record.slot_id)
            self._consumed[ticket.cap_key] = self._consumed.get(ticket.cap_key, 0) + 1
            self._records.append(record)
            return record

    def check_attempt(self, bank_id: str, attempt: int) -> None:
        """Refuse a Study B attempt beyond the 4th (`AttemptCapExceeded`, logged as an
        `attempt_cap` refusal with cap key `B|<bank_id>`). The bank builder (#26) calls it
        before starting an attempt."""
        check_id(bank_id, "bank ID")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise ValueError(f"attempt must be a positive integer, got {attempt!r}")
        if attempt > B_MAX_ATTEMPTS:
            with self._lock:
                self._refuse(
                    Study.B,
                    Method.B,
                    f"B|{bank_id}",
                    "attempt_cap",
                    f"attempt-{attempt}",
                    B_MAX_ATTEMPTS,
                    f"at most {B_MAX_ATTEMPTS} attempts per bank version",
                )
            raise AttemptCapExceeded(f"{bank_id}: attempt {attempt} > {B_MAX_ATTEMPTS}")

    def used(self, cap_key: str) -> int:
        """Slots consumed or open under `cap_key`."""
        with self._lock:
            return self._used(cap_key)

    def remaining(self, cap_key: str) -> int:
        """`cap - used(cap_key)`."""
        return max(0, self.cap - self.used(cap_key))

    def records(self, cap_key: str | None = None) -> tuple[SlotRecord, ...]:
        """Records in ledger order (all, or one cap key's)."""
        with self._lock:
            if cap_key is None:
                return tuple(self._records)
            return tuple(r for r in self._records if r.cap_key == cap_key)

    def open_tickets(self) -> tuple[SlotTicket, ...]:
        """Slots reserved and not yet consumed, in reservation order."""
        with self._lock:
            return tuple(self._open.values())
