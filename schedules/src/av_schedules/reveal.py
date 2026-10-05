"""Reveal-next stub for the operator console (#73): one list file plus an append-only log.

The console never reads a list directly. It opens a :class:`RevealLog` over the Study A
learner-facing slot list (``<set>-slots.json``) or the Study B dyad list
(``<set>-dyads.json``) and a JSON Lines log file, and:

1. logs an eligibility record (Study A: one coded participant after consent, apparatus
   compatibility and the 8/8 orientation check; Study B: both partners, in the order
   they finished screening, after compatibility and scheduling are recorded);
2. calls :meth:`RevealLog.reveal_next` with that record's ID, which binds the next
   unrevealed entry to the participant(s) and returns it.

Rules: entries are revealed strictly in list order, each at most once; a revealed slot is
never re-issued (vacancies after withdrawal are not refilled); every reveal consumes one
eligibility record; the restricted Study A book key is refused, and every Study A entry is
scanned for method strings before it is returned. Study B bank outages are logged before
the first reveal; a main dyad slot whose bank is unavailable is replaced, at its
position, by the first unused spare slot with the same SQ arm and swap flag.

Each log line is canonical JSON (sorted keys, no spaces) with ``line``, ``event``,
``at``, ``staff``, ``list_sha256`` and ``prev_sha256`` (SHA-256 of the previous line's
bytes; 64 zeros for the first), so edits and truncation are detected on load.

This is a stub: it assumes one console process per log and a trusted file system. The
file-format agreement with the console owner (#73) is pending.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from .assign_output import A_KEY_FORMAT, A_SLOTS_FORMAT, B_DYADS_FORMAT, load_list
from .masking import find_method_strings

LOG_FORMAT: Final = "av-schedules/reveal-log"
A_CHECKS: Final[tuple[str, ...]] = ("consent", "compatibility", "orientation")
B_CHECKS: Final[tuple[str, ...]] = ("consent", "screening", "compatibility", "scheduling")
GENESIS: Final = "0" * 64
_CODE_RE: Final = re.compile(r"[A-Za-z0-9._-]{1,32}")


class RevealError(RuntimeError):
    """A reveal request that the rules do not allow, or a damaged list or log."""


def utc_now() -> str:
    """Default clock: UTC time in ISO 8601 with seconds (``2027-01-08T14:03:00+00:00``)."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _code(value: str, what: str) -> str:
    if not isinstance(value, str) or not _CODE_RE.fullmatch(value):
        raise RevealError(f"{what} {value!r} must be 1-32 characters of [A-Za-z0-9._-]")
    return value


def _line_bytes(line: Mapping[str, Any]) -> bytes:
    text = json.dumps(line, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return (text + "\n").encode("utf-8")


class RevealLog:
    """Reveal-next API over one allocation list and its append-only reveal log."""

    def __init__(
        self, list_path: Path, log_path: Path, *, clock: Callable[[], str] = utc_now
    ) -> None:
        try:
            doc = load_list(list_path)
        except ValueError as exc:
            raise RevealError(str(exc)) from exc
        fmt = doc["format"]
        if fmt == A_KEY_FORMAT:
            raise RevealError("the restricted book key cannot be opened by the reveal API")
        self._format: str = fmt
        self._study: str = doc["study"]
        self._set: str = doc["set"]
        self._list_sha256: str = doc["list_sha256"]
        self._seed_label: str = doc["seed_label"]
        self._demo: bool = doc["demo"]
        self._log_path = log_path
        self._clock = clock
        self._entries: list[dict[str, Any]] = []
        self._spares: list[dict[str, Any]] = []
        if fmt == A_SLOTS_FORMAT:
            self._entries = sorted(doc["slots"], key=lambda e: e["order"])
            for entry in self._entries:
                found = find_method_strings(json.dumps(entry, sort_keys=True))
                if found:
                    raise RevealError(f"{entry['slot_id']} carries method strings {found}")
        elif fmt == B_DYADS_FORMAT:
            dyads = sorted(doc["dyads"], key=lambda e: e["order"])
            self._entries = [d for d in dyads if d["kind"] == "dyad"]
            self._spares = [d for d in dyads if d["kind"] == "spare"]
        self._prev = GENESIS
        self._n_lines = 0
        self._eligibility: dict[str, dict[str, Any]] = {}
        self._consumed: set[str] = set()
        self._people: set[str] = set()
        self._reveals: list[dict[str, Any]] = []
        self._unavailable: set[str] = set()
        self._used_spares: set[str] = set()
        self._replay()

    # -- properties --------------------------------------------------------------------

    @property
    def study(self) -> str:
        return self._study

    @property
    def set_name(self) -> str:
        return self._set

    @property
    def list_sha256(self) -> str:
        return self._list_sha256

    def revealed(self) -> list[dict[str, Any]]:
        """Entries revealed so far, in reveal order, with participant IDs bound."""
        return copy.deepcopy(self._reveals)

    def matches_source(
        self, *, study: str, set_name: str, demo: bool, seed_label: str, list_sha256: str
    ) -> bool:
        """Bind a configured visit without exposing private source labels in UI DTOs.

        The caller independently pins the authorized list hash alongside its
        run-sheet manifest hash. Study/set IDs alone can repeat across seeds.
        """
        return (
            type(demo) is bool
            and type(self._demo) is bool
            and (study, set_name, demo, seed_label, list_sha256)
            == (self._study, self._set, self._demo, self._seed_label, self._list_sha256)
        )

    def pending(self) -> list[str]:
        """Eligibility record IDs logged but not yet used by a reveal."""
        return [e for e in self._eligibility if e not in self._consumed]

    def remaining(self) -> int:
        """Number of list positions not yet revealed."""
        return len(self._entries) - len(self._reveals)

    # -- events ------------------------------------------------------------------------

    def log_eligibility(
        self, participant_ids: Sequence[str], *, staff: str, checks: Mapping[str, bool]
    ) -> str:
        """Log an eligibility record and return its ID (``E0001``...).

        Study A: one participant ID; ``checks`` must set ``consent``, ``compatibility`` and
        ``orientation`` to true. Study B: two participant IDs, first the one who finished
        screening first (member slot 1); ``checks`` must set ``consent``, ``screening``,
        ``compatibility`` and ``scheduling`` to true.
        """
        _code(staff, "staff code")
        people = [_code(p, "participant ID") for p in participant_ids]
        need = 1 if self._study == "A" else 2
        if len(people) != need or len(set(people)) != need:
            raise RevealError(
                f"Study {self._study} eligibility needs {need} distinct participant(s)"
            )
        used = sorted(set(people) & self._people)
        if used:
            raise RevealError(f"participant(s) {used} already have an eligibility record")
        required = A_CHECKS if self._study == "A" else B_CHECKS
        if any(not isinstance(v, bool) for v in checks.values()):
            raise RevealError("eligibility checks must be true or false")
        failed = [c for c in required if checks.get(c) is not True]
        if failed:
            raise RevealError(f"eligibility checks not passed: {failed}")
        record_id = f"E{len(self._eligibility) + 1:04d}"
        self._append(
            {
                "event": "eligibility",
                "staff": staff,
                "eligibility_id": record_id,
                "participant_ids": people,
                "checks": {k: checks[k] for k in sorted(checks)},
            }
        )
        return record_id

    def log_bank_unavailable(self, bank_id: str, *, staff: str) -> None:
        """Study B: record that a bank is unavailable (only before the first reveal)."""
        _code(staff, "staff code")
        if self._study != "B":
            raise RevealError("bank outages apply to Study B lists only")
        if self._reveals:
            raise RevealError("bank outages must be logged before the first reveal")
        banks = {d["bank_id"] for d in self._entries + self._spares}
        if bank_id not in banks:
            raise RevealError(f"unknown bank ID {bank_id!r}")
        if bank_id in self._unavailable:
            raise RevealError(f"{bank_id} is already logged as unavailable")
        self._append({"event": "bank_unavailable", "staff": staff, "bank_id": bank_id})

    def reveal_next(self, eligibility_id: str, *, staff: str) -> dict[str, Any]:
        """Reveal the next entry for an unused eligibility record and log the reveal."""
        _code(staff, "staff code")
        record = self._eligibility.get(eligibility_id)
        if record is None:
            raise RevealError(f"no eligibility record {eligibility_id!r}; log eligibility first")
        if eligibility_id in self._consumed:
            raise RevealError(f"eligibility record {eligibility_id} was already used")
        if self.remaining() <= 0:
            raise RevealError("the list is exhausted")
        entry = self._next_entry(record["participant_ids"])
        self._append(
            {"event": "reveal", "staff": staff, "eligibility_id": eligibility_id, "entry": entry}
        )
        return copy.deepcopy(entry)

    # -- internals ---------------------------------------------------------------------

    def _next_entry(self, people: Sequence[str]) -> dict[str, Any]:
        position = len(self._reveals)
        base = self._entries[position]
        if self._study == "A":
            entry = dict(base)
            entry["participant_id"] = people[0]
            found = find_method_strings(json.dumps(entry, sort_keys=True))
            if found:  # defensive: the list was checked on load
                raise RevealError(f"refusing to reveal method strings {found}")
            return entry
        chosen, replaces = base, None
        if base["bank_id"] in self._unavailable:
            chosen = self._spare_for(base)
            replaces = base["unit_id"]
        entry = copy.deepcopy(chosen)
        for member, person in zip(entry["members"], people, strict=True):
            member["participant_id"] = person
        entry["replaces"] = replaces
        return entry

    def _spare_for(self, base: Mapping[str, Any]) -> dict[str, Any]:
        for spare in self._spares:
            if (
                spare["unit_id"] not in self._used_spares
                and spare["bank_id"] not in self._unavailable
                and spare["sq_arm"] == base["sq_arm"]
                and spare["swap_w1_w4"] == base["swap_w1_w4"]
            ):
                return spare
        raise RevealError(f"no spare slot left to replace {base['unit_id']}; escalate")

    def _append(self, event: dict[str, Any]) -> None:
        at = self._clock()
        if not isinstance(at, str) or not at:
            raise RevealError("the clock must return a non-empty string")
        line = {
            "format": LOG_FORMAT,
            "line": self._n_lines + 1,
            "at": at,
            "list_sha256": self._list_sha256,
            "prev_sha256": self._prev,
            **event,
        }
        data = _line_bytes(line)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("ab") as fh:
            fh.write(data)
        self._accept(line, data)

    def _accept(self, line: Mapping[str, Any], data: bytes) -> None:
        self._prev = hashlib.sha256(data).hexdigest()
        self._n_lines += 1
        event = line["event"]
        if event == "eligibility":
            self._eligibility[line["eligibility_id"]] = dict(line)
            self._people.update(line["participant_ids"])
        elif event == "bank_unavailable":
            self._unavailable.add(line["bank_id"])
        elif event == "reveal":
            self._consumed.add(line["eligibility_id"])
            entry = line["entry"]
            self._reveals.append(copy.deepcopy(entry))
            if entry.get("replaces"):
                self._used_spares.add(entry["unit_id"])
        else:
            raise RevealError(f"unknown log event {event!r}")

    def _replay(self) -> None:
        if not self._log_path.exists():
            return
        raw = self._log_path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise RevealError("the reveal log ends with a partial line")
        for n, data in enumerate(raw.splitlines(keepends=True), start=1):
            try:
                line = json.loads(data)
            except json.JSONDecodeError as exc:
                raise RevealError(f"log line {n} is not JSON") from exc
            if _line_bytes(line) != data:
                raise RevealError(f"log line {n} is not in canonical form (edited?)")
            if line.get("format") != LOG_FORMAT or line.get("line") != n:
                raise RevealError(f"log line {n} has a wrong format or number")
            if line.get("prev_sha256") != self._prev:
                raise RevealError(f"log line {n} breaks the hash chain (edited or reordered?)")
            if line.get("list_sha256") != self._list_sha256:
                raise RevealError(f"log line {n} belongs to another list")
            self._accept(line, data)
