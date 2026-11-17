"""Operator-console audit and player operator logs retained with a native run (#293).

The ``ops.console`` audit is a hash-chained journal; in simulation mode the
console confines it to the run's evidence directory, so it is already inside the
run root. The player writes its operator logs (foundation, state-source, panel
and calibration journals, sample-age CSV) under its own ``operator-logs``
directory, outside the run root, and the emitter copies the files the harness
names into ``operator-logs/``.

Retained files must carry no answer field: no key may name the intended answer
or a correctness derived from it. Only keys are checked; values are not
interpreted. Bytes are never rewritten.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import PurePosixPath
import re

from .records import EvidenceError, require, sha, strict

KINDS = frozenset({"console_audit", "console_audit_tip", "operator_log"})
LOG_DIRECTORY = "operator-logs"
# The player names each log <kind>-<GUID "N">.<ext>, e.g. sample-age-<32 hex>.csv.
LOG_NAME = re.compile(r"[a-z][a-z0-9-]{0,62}-[0-9a-f]{32}\.(?:jsonl|csv)")
MAX_LOGS = 1000
ANSWER_TOKENS = frozenset({"answer", "answers", "correct", "correctness", "intended"})
ANSWER_SEQUENCES = ("target_action", "target_referent", "semantic_action", "semantic_referent")
AUDIT_FIELDS = {"sequence", "previous", "utc", "protocol", "staff", "event", "details"}


def is_log_path(name):
    """A run-root path that holds one retained player log."""
    path = PurePosixPath(name)
    return path.parent.name == LOG_DIRECTORY and LOG_NAME.fullmatch(path.name) is not None


def retained_kind(name, audit_name):
    if audit_name is not None and name == audit_name:
        return "console_audit"
    if audit_name is not None and name == audit_name + ".tip":
        return "console_audit_tip"
    return "operator_log" if is_log_path(name) else None


def answer_field(key):
    name = key.lower().replace("-", "_")
    tokens = {token for token in name.split("_") if token}
    return bool(tokens & ANSWER_TOKENS) or any(sequence in name for sequence in ANSWER_SEQUENCES)


def _json_keys(text):
    keys = []
    def collect(pairs):
        keys.extend(key for key, _ in pairs)
        return dict(pairs)
    for line in text.split("\n")[:-1]:
        value = json.loads(line.removesuffix("\r"), object_pairs_hook=collect, parse_constant=lambda _: None)
        require(isinstance(value, dict), "MOCK_OPERATOR_LOG_MALFORMED")
    return keys


def _csv_header(text):
    return next(csv.reader(io.StringIO(text, newline=""), strict=True), [])


def _refuse_answer_keys(raw, table):
    try:
        text = raw.decode("utf-8", errors="strict")
        require(not text or text.endswith("\n"), "MOCK_OPERATOR_LOG_MALFORMED")
        keys = _csv_header(text) if table else _json_keys(text)
    except (UnicodeError, json.JSONDecodeError, RecursionError, csv.Error) as error:
        raise EvidenceError("MOCK_OPERATOR_LOG_MALFORMED") from error
    require(not any(answer_field(key) for key in keys), "MOCK_OPERATOR_LOG_ANSWER_FIELD")


def answer_free(name, raw):
    """Return ``raw`` if no JSON key or CSV column of the named log is an answer field."""
    _refuse_answer_keys(raw, table=PurePosixPath(name).suffix == ".csv")
    return raw


def console_audit(raw, tip_raw, protocol):
    """Re-verify the closed console audit chain and its tip exactly as ``ops.console`` writes them."""
    require(raw.endswith(b"\n"), "MOCK_CONSOLE_AUDIT_CHAIN")
    rows, previous = [], "0" * 64
    for line in raw.splitlines(keepends=True):
        row = strict(line)
        require(isinstance(row, dict) and set(row) == AUDIT_FIELDS and type(row["sequence"]) is int
                and json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n" == line
                and row["sequence"] == len(rows) + 1 and row["previous"] == previous and row["protocol"] == protocol,
                "MOCK_CONSOLE_AUDIT_CHAIN")
        rows.append(row)
        previous = sha(line)
    require(strict(tip_raw) == {"sequence": len(rows), "sha256": previous}, "MOCK_CONSOLE_AUDIT_TIP")
    _refuse_answer_keys(raw, table=False)
    return rows


def verify(artifacts, protocol, schedule_sha256):
    """Integrity faults refuse; missing or unbound operator evidence stays incomplete."""
    incomplete = set()
    logs = artifacts["operator_log"]
    require(len(logs) <= MAX_LOGS, "MOCK_OPERATOR_LOG_LIMIT")
    for artifact, raw in logs:
        require(is_log_path(artifact["path"]), "MOCK_OPERATOR_LOG_NAME")
        answer_free(artifact["path"], raw)
    if not logs:
        incomplete.add("OPERATOR_LOGS_MISSING")
    audits, tips = artifacts["console_audit"], artifacts["console_audit_tip"]
    require(len(audits) <= 1 and len(tips) == len(audits), "MOCK_CONSOLE_AUDIT_COUNT")
    if not audits:
        incomplete.add("CONSOLE_AUDIT_MISSING")
        return incomplete
    (audit, raw), (tip, tip_raw) = audits[0], tips[0]
    require(tip["path"] == audit["path"] + ".tip", "MOCK_CONSOLE_AUDIT_TIP")
    rows = console_audit(raw, tip_raw, protocol)
    if not any(row["event"] == "load_requested" and row["details"] == {"schedule": schedule_sha256} for row in rows):
        incomplete.add("CONSOLE_AUDIT_SCHEDULE_UNBOUND")
    return incomplete
