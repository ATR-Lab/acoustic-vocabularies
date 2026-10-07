"""Durable allocation operator boundary. Receipt pins are a trusted handoff, not authentication."""

from __future__ import annotations

import copy
import os
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .admission_io import WriterLock, canonical, checked_path, parse, read, sha, sync_directory
from .assign_output import A_KEY_FORMAT, A_SLOTS_FORMAT, B_DYADS_FORMAT, canonical_sha256
from .masking import find_method_strings
from .reveal import A_CHECKS, B_CHECKS, GENESIS, LOG_FORMAT, RevealError, RevealLog, utc_now

MAX_LINES = 2048
MAX_JOURNAL = 8 * 1024 * 1024
HASH = re.compile(r"[0-9a-f]{64}")
CODE = re.compile(r"[A-Za-z0-9._-]{1,32}")
ORIENTATION_KEYS = {
    "schema_version",
    "receipt_type",
    "screening_id",
    "station_id",
    "protocol_version",
    "orientation_id",
    "plan_sha256",
    "demo_index_sha256",
    "journal_sha256",
    "journal_bytes",
    "outcome",
    "engineering_draft",
    "eligible",
    "receipt_sha256",
}


def _exact(value: object, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise RevealError("CLOSED_RECORD_REQUIRED")
    return value


def _hash(value: object) -> str:
    if not isinstance(value, str) or not HASH.fullmatch(value):
        raise RevealError("SHA256_REQUIRED")
    return value


def _receipt(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "receipt_sha256": sha(canonical(value))}


OUTCOMES = ("pass_first", "pass_second", "fail")


def validate_orientation(value: object) -> dict[str, Any]:
    """Admission gate: only a finalized, non-draft, passed receipt is accepted."""
    return _validate_orientation(value, require_eligible=True)


def _validate_orientation(value: object, *, require_eligible: bool) -> dict[str, Any]:
    result = _exact(value, ORIENTATION_KEYS)
    if type(result["schema_version"]) is not int or result["schema_version"] != 1:
        raise RevealError("ORIENTATION_VERSION")
    if result["receipt_type"] != "orientation-outcome":
        raise RevealError("ORIENTATION_TYPE")
    for key in ("plan_sha256", "demo_index_sha256", "journal_sha256", "receipt_sha256"):
        _hash(result[key])
    for key in ("screening_id", "station_id", "protocol_version", "orientation_id"):
        value = result[key]
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", value):
            raise RevealError("ORIENTATION_ID")
    if not CODE.fullmatch(result["screening_id"]):
        raise RevealError("SCREENING_ID")
    if type(result["journal_bytes"]) is not int or not 0 < result["journal_bytes"] <= MAX_JOURNAL:
        raise RevealError("ORIENTATION_JOURNAL_SIZE")
    if require_eligible and (
        result["engineering_draft"] is not False
        or result["eligible"] is not True
        or result["outcome"] not in {"pass_first", "pass_second"}
    ):
        raise RevealError("ORIENTATION_NOT_ELIGIBLE")
    draft, eligible = result["engineering_draft"], result["eligible"]
    if (
        result["outcome"] not in OUTCOMES
        or type(draft) is not bool
        or type(eligible) is not bool
        or eligible is not (result["outcome"] != "fail" and not draft)
    ):
        raise RevealError("ORIENTATION_OUTCOME_INVALID")
    expected = _receipt({k: v for k, v in result.items() if k != "receipt_sha256"})
    if expected != result:
        raise RevealError("ORIENTATION_RECEIPT_HASH")
    return result


def read_orientation(receipt_path: Path, raw_sha256: str, journal_path: Path) -> dict[str, Any]:
    """Admission gate: verified evidence for a finalized, non-draft passed orientation."""
    return _read_orientation(receipt_path, raw_sha256, journal_path, require_eligible=True)


def inspect_orientation(receipt_path: Path, raw_sha256: str, journal_path: Path) -> dict[str, Any]:
    """Display-only reader: the same integrity checks, also for recorded fail/draft outcomes.

    The result is never admission evidence; only ``read_orientation`` grants eligibility.
    """
    return _read_orientation(receipt_path, raw_sha256, journal_path, require_eligible=False)


def _read_orientation(
    receipt_path: Path, raw_sha256: str, journal_path: Path, *, require_eligible: bool
) -> dict[str, Any]:
    data = read(receipt_path, 65536)
    if sha(data) != _hash(raw_sha256):
        raise RevealError("ORIENTATION_FILE_HASH")
    receipt = _validate_orientation(parse(data), require_eligible=require_eligible)
    journal = read(journal_path)
    if len(journal) != receipt["journal_bytes"] or sha(journal) != receipt["journal_sha256"]:
        raise RevealError("ORIENTATION_JOURNAL_HASH")
    if not journal.endswith(b"\n"):
        raise RevealError("ORIENTATION_JOURNAL_PARTIAL")
    rows: list[dict[str, Any]] = []
    for line in journal.splitlines():
        row = parse(line)
        if not isinstance(row, dict):
            raise RevealError("ORIENTATION_JOURNAL_BINDING")
        rows.append(row)
    if not rows:
        raise RevealError("ORIENTATION_JOURNAL_BINDING")
    header, final = rows[0], rows[-1]
    if (
        header.get("event") != "orientation_header"
        or header.get("preallocation") is not True
        or header.get("study_audio_loaded") is not False
        or any(
            header.get(k) != receipt[k]
            for k in (
                "screening_id",
                "orientation_id",
                "protocol_version",
                "station_id",
                "plan_sha256",
                "demo_index_sha256",
            )
        )
        or final.get("event") != "eligibility_outcome"
        or final.get("outcome") != receipt["outcome"]
        or final.get("engineering_draft") is not receipt["engineering_draft"]
        or final.get("preallocation") is not True
        or final.get("learning_result") is not False
    ):
        raise RevealError("ORIENTATION_JOURNAL_BINDING")
    first, second = final.get("first_correct"), final.get("second_correct")
    if (
        not isinstance(first, list)
        or len(first) != 8
        or any(type(x) is not bool for x in first)
        or not isinstance(second, list)
        or any(type(x) is not bool for x in second)
    ):
        raise RevealError("ORIENTATION_CHECK_COUNTS")
    if receipt["outcome"] == "pass_first":
        valid = all(first) and second == [] and final.get("reexplanations") == 0
    else:
        # pass_second needs 8/8 on the second check; fail is any other second score.
        valid = (
            not all(first)
            and len(second) == 8
            and all(second) is (receipt["outcome"] == "pass_second")
            and final.get("reexplanations") == 1
        )
    if not valid:
        raise RevealError("ORIENTATION_CHECK_COUNTS")
    for attempt, answers in ((1, first), (2, second)):
        responses = [
            r for r in rows if r.get("event") == "practice_response" and r.get("attempt") == attempt
        ]
        if (
            len(responses) != len(answers)
            or [r.get("ordinal") for r in responses] != list(range(1, len(answers) + 1))
            or any(
                r.get("correct") is not expected
                for r, expected in zip(responses, answers, strict=True)
            )
        ):
            raise RevealError("ORIENTATION_RESPONSE_EVIDENCE")
    return copy.deepcopy(receipt)


class _Policy(RevealLog):
    """Reuse deterministic policy without the legacy class's file-writing surface."""

    def __init__(self, doc: dict[str, Any], clock: Callable[[], str]) -> None:
        self._format = doc["format"]
        self._study, self._set = doc["study"], doc["set"]
        self._list_sha256 = doc["list_sha256"]
        self._clock = clock
        if self._format == A_SLOTS_FORMAT:
            self._entries = sorted(copy.deepcopy(doc["slots"]), key=lambda e: e["order"])
            self._spares = []
            if find_method_strings(canonical(self._entries).decode()):
                raise RevealError("METHOD_LABEL_FORBIDDEN")
        else:
            dyads = sorted(copy.deepcopy(doc["dyads"]), key=lambda e: e["order"])
            self._entries = [d for d in dyads if d["kind"] == "dyad"]
            self._spares = [d for d in dyads if d["kind"] == "spare"]
        self._prev, self._n_lines = GENESIS, 0
        self._eligibility = {}
        self._consumed = set()
        self._people = set()
        self._reveals = []
        self._unavailable = set()
        self._used_spares = set()
        self.last: dict[str, Any] = {}

    def _append(self, event: dict[str, Any]) -> None:
        at = self._clock()
        if not isinstance(at, str) or not at or len(at) > 80:
            raise RevealError("CLOCK_REQUIRED")
        self.last = {
            "format": LOG_FORMAT,
            "line": self._n_lines + 1,
            "at": at,
            "list_sha256": self._list_sha256,
            "prev_sha256": self._prev,
            **event,
        }
        # The policy's chain is deliberately separate from the stronger outer chain.
        from .reveal import _line_bytes

        self._accept(self.last, _line_bytes(self.last))


class DurableRevealLog:
    """One private journal; explicit independently retained head required on every open.

    Operations serialize across processes and replay the complete current journal under
    the OS lock. A stale caller pin is allowed only if it occurs in the intact chain.
    Whole-file rollback behind that pin fails, even if checkpoint and journal agree.
    """

    def __init__(
        self,
        list_path: Path,
        list_file_sha256: str,
        journal_path: Path,
        *,
        expected_head: str,
        recover_tail: bool = False,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self.journal_path = checked_path(journal_path, missing=True)
        if not any(
            p.lower() in {"private", ".local", "local-data"} for p in journal_path.parent.parts[2:]
        ):
            raise RevealError("PRIVATE_JOURNAL_REQUIRED")
        self.checkpoint_path = journal_path.with_name(journal_path.name + ".head.json")
        self.lock_path = journal_path.with_name(journal_path.name + ".lock")
        self.list_path = list_path
        self.list_file_sha256 = _hash(list_file_sha256)
        self.head = _hash(expected_head)
        self.clock = clock
        self.faulted = False
        with WriterLock(self.lock_path):
            self._load(recover_tail)

    def _load(self, recover: bool = False) -> None:
        if self.faulted:
            raise RevealError("REOPEN_REQUIRED")
        data = read(self.list_path, 4 * 1024 * 1024)
        if sha(data) != self.list_file_sha256:
            raise RevealError("LIST_FILE_CHANGED")
        doc = parse(data)
        if (
            not isinstance(doc, dict)
            or doc.get("format") not in {A_SLOTS_FORMAT, B_DYADS_FORMAT}
            or doc.get("format") == A_KEY_FORMAT
            or doc.get("list_sha256") != canonical_sha256(doc)
        ):
            raise RevealError("ALLOCATION_LIST_INVALID")
        self.doc = doc
        self.policy = _Policy(self.doc, self.clock)
        self.rows: list[dict[str, Any]] = []
        self.hashes = [GENESIS]
        self.ends = [0]
        self.eligibility: dict[str, dict[str, Any]] = {}
        self.reveals: dict[str, dict[str, Any]] = {}
        self.raw = read(self.journal_path) if self.journal_path.exists() else b""
        if self.raw and not self.raw.endswith(b"\n"):
            raise RevealError("JOURNAL_PARTIAL_LINE")
        for data in self.raw.splitlines(keepends=True):
            if len(self.rows) >= MAX_LINES:
                raise RevealError("JOURNAL_LINE_LIMIT")
            row = _exact(
                parse(data),
                {
                    "schema_version",
                    "sequence",
                    "previous_sha256",
                    "list_sha256",
                    "policy",
                    "evidence",
                },
            )
            if (
                type(row["schema_version"]) is not int
                or row["schema_version"] != 1
                or type(row["sequence"]) is not int
                or row["sequence"] != len(self.rows) + 1
                or row["previous_sha256"] != self.hashes[-1]
                or row["list_sha256"] != self.doc["list_sha256"]
                or canonical(row) + b"\n" != data
            ):
                raise RevealError("JOURNAL_CHAIN_INVALID")
            self._replay_policy(row)
            self._remember(row, data)
        if self.head not in self.hashes:
            raise RevealError("RETAINED_HEAD_MISSING")
        if self.checkpoint_path.exists():
            checkpoint = _exact(
                parse(read(self.checkpoint_path, 4096)),
                {
                    "schema_version",
                    "list_sha256",
                    "journal_line",
                    "journal_head_sha256",
                    "journal_bytes",
                },
            )
            count = checkpoint["journal_line"]
            if (
                type(count) is not int
                or type(checkpoint["schema_version"]) is not int
                or type(checkpoint["journal_bytes"]) is not int
                or not 0 <= count < len(self.hashes)
                or checkpoint != self._checkpoint(count)
            ):
                raise RevealError("CHECKPOINT_MISMATCH")
            if count != len(self.rows):
                if not recover or len(self.rows) != count + 1:
                    raise RevealError("COMPLETE_TAIL_RECOVERY_REQUIRED")
                with self.journal_path.open("r+b") as stream:
                    os.fsync(stream.fileno())
                self._write_checkpoint(self._checkpoint(len(self.rows)))
        elif self.raw:
            raise RevealError("CHECKPOINT_MISSING")
        else:
            self._write_checkpoint(self._checkpoint(0))
        self.head = self.hashes[-1]

    def _replay_policy(self, row: dict[str, Any]) -> None:
        policy = row["policy"]
        if not isinstance(policy, dict) or not isinstance(policy.get("at"), str):
            raise RevealError("POLICY_RECORD_INVALID")
        self.policy._clock = lambda: policy["at"]
        event = policy.get("event")
        evidence = row["evidence"]
        if event == "eligibility":
            _exact(evidence, {"orientation_receipts"})
            people = policy.get("participant_ids")
            receipts = evidence["orientation_receipts"]
            self._validate_people(people, policy.get("checks"), receipts)
            self.policy.log_eligibility(
                policy["participant_ids"], staff=policy["staff"], checks=policy["checks"]
            )
        elif event == "reveal":
            _exact(evidence, {"eligibility_receipt_sha256"})
            previous = self.eligibility.get(policy["eligibility_id"])
            if (
                previous is None
                or previous["receipt_sha256"] != evidence["eligibility_receipt_sha256"]
            ):
                raise RevealError("ELIGIBILITY_RECEIPT_MISMATCH")
            self.policy.reveal_next(policy["eligibility_id"], staff=policy["staff"])
        elif event == "bank_unavailable":
            _exact(evidence, set())
            self.policy.log_bank_unavailable(policy["bank_id"], staff=policy["staff"])
        else:
            raise RevealError("EVENT_INVALID")
        if self.policy.last != policy:
            raise RevealError("POLICY_REPLAY_MISMATCH")
        self.policy._clock = self.clock

    def _validate_people(self, people: object, checks: object, receipts: object) -> None:
        need = 1 if self.doc["study"] == "A" else 2
        required = A_CHECKS if need == 1 else B_CHECKS
        if (
            not isinstance(people, list)
            or len(people) != need
            or len(set(people)) != need
            or not isinstance(receipts, list)
            or len(receipts) != need
            or not isinstance(checks, dict)
            or set(checks) != set(required)
            or any(value is not True for value in checks.values())
        ):
            raise RevealError("ELIGIBILITY_BINDING_INVALID")
        for person, receipt in zip(people, receipts, strict=True):
            if validate_orientation(receipt)["screening_id"] != person:
                raise RevealError("SCREENING_RECEIPT_MISMATCH")

    def _remember(self, row: dict[str, Any], data: bytes) -> None:
        self.rows.append(row)
        self.hashes.append(sha(data))
        self.ends.append(self.ends[-1] + len(data))
        p = row["policy"]
        common = {
            "schema_version": 1,
            "study": self.doc["study"],
            "set": self.doc["set"],
            "list_sha256": self.doc["list_sha256"],
            "eligibility_id": p.get("eligibility_id"),
            "journal_head_sha256": self.hashes[-1],
            "journal_line": len(self.rows),
        }
        if p["event"] == "eligibility":
            self.eligibility[p["eligibility_id"]] = _receipt(
                {
                    **common,
                    "receipt_type": "allocation-eligibility",
                    "screening_ids": p["participant_ids"],
                    "orientation_receipt_sha256": [
                        r["receipt_sha256"] for r in row["evidence"]["orientation_receipts"]
                    ],
                }
            )
        elif p["event"] == "reveal":
            eligibility = self.eligibility[p["eligibility_id"]]
            self.reveals[p["eligibility_id"]] = _receipt(
                {
                    **common,
                    "receipt_type": "allocation-reveal",
                    "eligibility_receipt_sha256": eligibility["receipt_sha256"],
                    "screening_ids": eligibility["screening_ids"],
                    "entry": p["entry"],
                    "entry_sha256": sha(canonical(p["entry"])),
                }
            )

    def _checkpoint(self, count: int) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "list_sha256": self.doc["list_sha256"],
            "journal_line": count,
            "journal_head_sha256": self.hashes[count],
            "journal_bytes": self.ends[count],
        }

    def _write_checkpoint(self, value: dict[str, Any]) -> None:
        checked_path(self.checkpoint_path, missing=True)
        temporary = self.checkpoint_path.with_name("." + uuid.uuid4().hex + ".tmp")
        with os.fdopen(
            os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
        ) as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.checkpoint_path)
        sync_directory(self.checkpoint_path.parent)

    def _append(self, evidence: dict[str, Any]) -> None:
        row = {
            "schema_version": 1,
            "sequence": len(self.rows) + 1,
            "previous_sha256": self.hashes[-1],
            "list_sha256": self.doc["list_sha256"],
            "policy": self.policy.last,
            "evidence": evidence,
        }
        data = canonical(row) + b"\n"
        if len(self.raw) + len(data) > MAX_JOURNAL or len(self.rows) >= MAX_LINES:
            raise RevealError("JOURNAL_LIMIT")
        checked_path(self.journal_path, missing=True)
        try:
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_APPEND
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            with os.fdopen(os.open(self.journal_path, flags, 0o600), "ab") as stream:
                if os.fstat(stream.fileno()).st_size != len(self.raw):
                    raise RevealError("JOURNAL_CHANGED")
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            sync_directory(self.journal_path.parent)
            self._remember(row, data)
            self._write_checkpoint(self._checkpoint(len(self.rows)))
            self.head = self.hashes[-1]
        except Exception:
            self.faulted = True
            raise

    def log_eligibility(
        self,
        screening_ids: Sequence[str],
        *,
        staff: str,
        checks: Mapping[str, bool],
        orientation_files: Sequence[tuple[Path, str, Path]],
    ) -> dict[str, Any]:
        with WriterLock(self.lock_path):
            self._load()
            receipts = [read_orientation(*item) for item in orientation_files]
            people = list(screening_ids)
            self._validate_people(people, dict(checks), receipts)
            # A lost reply may be recovered without creating a second eligibility.
            for row in self.rows:
                p = row["policy"]
                if p["event"] == "eligibility" and p["participant_ids"] == people:
                    if (
                        p["checks"] != dict(checks)
                        or row["evidence"]["orientation_receipts"] != receipts
                    ):
                        raise RevealError("ELIGIBILITY_RETRY_MISMATCH")
                    return copy.deepcopy(self.eligibility[p["eligibility_id"]])
            self.policy.log_eligibility(people, staff=staff, checks=checks)
            self._append({"orientation_receipts": receipts})
            return copy.deepcopy(self.eligibility[self.policy.last["eligibility_id"]])

    def reveal_next(
        self, eligibility_id: str, *, eligibility_receipt_sha256: str, staff: str
    ) -> dict[str, Any]:
        with WriterLock(self.lock_path):
            self._load()
            previous = self.eligibility.get(eligibility_id)
            if previous is None or previous["receipt_sha256"] != _hash(eligibility_receipt_sha256):
                raise RevealError("ELIGIBILITY_RECEIPT_MISMATCH")
            if eligibility_id in self.reveals:
                return copy.deepcopy(self.reveals[eligibility_id])
            self.policy.reveal_next(eligibility_id, staff=staff)
            self._append({"eligibility_receipt_sha256": eligibility_receipt_sha256})
            return copy.deepcopy(self.reveals[eligibility_id])

    def log_bank_unavailable(self, bank_id: str, *, staff: str) -> str:
        with WriterLock(self.lock_path):
            self._load()
            self.policy.log_bank_unavailable(bank_id, staff=staff)
            self._append({})
            return self.head

    def eligibility_receipt(self, eligibility_id: str) -> dict[str, Any]:
        with WriterLock(self.lock_path):
            self._load()
            if eligibility_id not in self.eligibility:
                raise RevealError("ELIGIBILITY_MISSING")
            return copy.deepcopy(self.eligibility[eligibility_id])
