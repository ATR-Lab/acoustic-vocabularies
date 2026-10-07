"""Operator admission and sanitized view. Private inputs never serialize wholesale."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from av_schedules.masking import find_method_strings
from av_schedules.planning import RUN_SHEET_COLUMNS, TEMPLATE_SHA256, RUN_SHEET_TEMPLATE
from av_schedules.run_sheets import read_run_sheet, run_sheet_findings, parse_package_hashes


class ConsoleFault(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise ConsoleFault(code)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def strict_json(data):
    def pairs(items):
        out = {}
        for key, value in items:
            require(key not in out, "invalid_record")
            out[key] = value
        return out
    try:
        return json.loads(data, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ConsoleFault("invalid_record")))
    except (ValueError, UnicodeError):
        raise ConsoleFault("invalid_record") from None


def code(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value), "invalid_code")
    require(not find_method_strings(value), "masked_value_rejected")
    return value


def hash_value(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value), "hash_invalid")
    return value


def masked(value):
    require(not find_method_strings(value if isinstance(value, str) else encoded(value).decode()), "masked_value_rejected")
    return value


def utc_now():
    return datetime.now(timezone.utc)


def instant(text):
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
        require(value.tzinfo is not None, "time_invalid")
        return value.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise ConsoleFault("time_invalid") from None


def visit_window(study, visit, anchors, day, role=None, now=None):
    """Calendar-day windows; dyad ordering is an elapsed UTC interval."""
    windows = {("A", "D7"): ("D0", 7, 1), ("B", "V2"): ("V1", 2, 1),
               ("B", "V3"): ("V1", 4, 1), ("B", "W1"): ("V3", 7, 1),
               ("B", "W4"): ("V3", 28, 2)}
    require((study == "A" and visit in ("D0", "D7")) or
            (study == "B" and visit in ("V1", "V2", "V3", "W1", "W4")), "visit_invalid")
    faults = []
    lower = upper = None
    if (study, visit) in windows:
        anchor, offset, tolerance = windows[study, visit]
        require(anchor in anchors, "anchor_missing")
        try:
            base = date.fromisoformat(anchors[anchor])
        except (ValueError, TypeError):
            raise ConsoleFault("anchor_invalid") from None
        lower, upper = base + timedelta(days=offset-tolerance), base + timedelta(days=offset+tolerance)
        if not lower <= day <= upper:
            faults.append("visit_window")
    if study == "B" and role == "yoked":
        require("active" in anchors and now is not None, "active_anchor_missing")
        elapsed = (now - instant(anchors["active"])).total_seconds()
        if not 0 <= elapsed <= 86400:
            faults.append("pair_window")
    return dict(start=lower.isoformat() if lower else None, end=upper.isoformat() if upper else None,
                faults=faults)


@dataclass(frozen=True)
class Visit:
    participant: str
    study: str
    visit: str
    book: str
    role: str | None
    demo: bool
    manifest_hash: str
    schedule_hash: str
    package_hash: str
    rows: tuple
    anchors: dict


def load_bundle(config, reveals):
    """Configured paths only. Verify producer chain; engine independently loads PCM."""
    def read(key):
        p = Path(config[key])
        require(p.is_file() and not p.is_symlink() and p.stat().st_size <= 8_000_000, "input_invalid")
        return p.read_bytes()
    manifest_bytes, schedules_bytes, schedule_bytes, sheet_bytes, packages_bytes = (
        read(k) for k in ("manifest", "schedules", "schedule", "sheet", "packages"))
    expected = hash_value(config["manifest_sha256"])
    require(digest(manifest_bytes) == expected, "manifest_hash_mismatch")
    manifest, schedules, schedule = (strict_json(x) for x in (manifest_bytes, schedules_bytes, schedule_bytes))
    require(manifest.get("format") == "av-schedules/run-sheets-manifest" and
            schedules.get("format") == "av-schedules/schedules-manifest", "chain_invalid")
    require(type(manifest.get("format_version")) is int and manifest["format_version"] == 1 and
            type(schedules.get("format_version")) is int and schedules["format_version"] == 1, "chain_invalid")
    template = manifest.get("template", {})
    require(template.get("columns") == list(RUN_SHEET_COLUMNS) and
            template.get("sha256") == TEMPLATE_SHA256[RUN_SHEET_TEMPLATE], "template_invalid")
    require(manifest.get("schedules_manifest_sha256") == digest(schedules_bytes) and
            manifest.get("package_hashes", {}).get("sha256") == digest(packages_bytes) and
            manifest.get("checks", {}).get("findings") == 0, "chain_invalid")
    hashes = parse_package_hashes(packages_bytes)
    require(not hashes.placeholder, "placeholder_hash")
    require(type(schedule.get("demo")) is bool, "chain_invalid")
    for key in ("study", "set", "demo", "seed_label"):
        require(schedule.get(key) == manifest.get(key) == schedules.get(key), "identity_mismatch")
    require((hashes.study, hashes.set_name, hashes.demo) ==
            (schedule["study"], schedule["set"], schedule["demo"]), "identity_mismatch")
    require(reveals.matches_source(study=schedule["study"], set_name=schedule["set"],
                                  demo=schedule["demo"], seed_label=schedule["seed_label"],
                                  list_sha256=hash_value(config["allocation_list_sha256"])), "allocation_source_mismatch")
    person, unit, visit = (code(schedule[k]) for k in ("person_id", "unit_id", "visit"))
    participant = code(config["participant"])
    matches = []
    for entry in reveals.revealed():
        if schedule["study"] == "A" and entry.get("slot_id") == person and entry.get("participant_id") == participant:
            matches.append((entry["book_id"], None))
        if schedule["study"] == "B" and entry.get("unit_id") == unit:
            for member in entry.get("members", []):
                if member.get("person_id", member.get("slot_id")) == person and member.get("participant_id") == participant:
                    matches.append((unit, member.get("role")))
    require(len(matches) == 1 and reveals.study == schedule["study"] and reveals.set_name == schedule["set"], "allocation_unbound")
    book, role = matches[0]
    require(role in (None, "active", "yoked"), "allocation_unbound")
    package_cell = hashes.cell(book)
    require(package_cell is not None and package_cell.startswith("sha256:"), "package_hash_missing")
    require(schedules.get("files", {}).get(f"{unit}/schedules/{person}/{visit}.json") == digest(schedule_bytes) and
            manifest.get("files", {}).get(f"{unit}/run-sheets/{person}/{visit}.csv") == digest(sheet_bytes), "file_hash_mismatch")
    require(not run_sheet_findings(sheet_bytes, schedule, package_cell), "sheet_invalid")
    _, rows = read_run_sheet(sheet_bytes)
    for row in rows:
        row["participant_id"] = participant
    masked(rows)
    return Visit(participant, schedule["study"], visit, code(book), role, schedule["demo"], expected,
                 digest(schedule_bytes), hash_value(package_cell[7:]), tuple(rows), dict(config["anchors"]))


class Audit:
    """One writer, fsync before acknowledgement. Refuse partial/edited history."""
    def __init__(self, path, protocol, clock=utc_now):
        self.path, self.protocol, self.clock = Path(path), code(protocol), clock
        self.rows, self.prev, self.failed = [], "0"*64, False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.tip_path = self.path.with_suffix(self.path.suffix+".tip")
        raw = self.path.read_bytes() if self.path.exists() else b""
        require(not raw or raw.endswith(b"\n"), "audit_damaged")
        for line in raw.splitlines(keepends=True):
            row = strict_json(line)
            require(encoded(row)+b"\n" == line and row.get("sequence") == len(self.rows)+1 and
                    row.get("previous") == self.prev and row.get("protocol") == self.protocol, "audit_damaged")
            masked(row)
            self.rows.append(row)
            self.prev = digest(line)
        if raw or self.tip_path.exists():
            require(self.tip_path.is_file(), "audit_damaged")
            tip = strict_json(self.tip_path.read_bytes())
            require(tip == dict(sequence=len(self.rows), sha256=self.prev), "audit_damaged")

    def append(self, event, staff, details):
        require(not self.failed, "audit_failed")
        row = dict(sequence=len(self.rows)+1, previous=self.prev, utc=self.clock().isoformat(),
                   protocol=self.protocol, staff=code(staff), event=code(event), details=details)
        masked(row)
        data = encoded(row)+b"\n"
        try:
            with self.path.open("ab") as stream:
                require(stream.write(data) == len(data), "audit_failed")
                stream.flush()
                os.fsync(stream.fileno())
            tip = encoded(dict(sequence=len(self.rows)+1, sha256=digest(data)))
            temp = self.tip_path.with_suffix(self.tip_path.suffix+".tmp")
            with temp.open("wb") as stream:
                require(stream.write(tip) == len(tip), "audit_failed")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.tip_path)
        except (OSError, ConsoleFault):
            self.failed = True
            raise ConsoleFault("audit_failed") from None
        self.rows.append(row)
        self.prev = digest(data)
        return str(row["sequence"])


HEALTH_FLAGS = ("headset", "audio", "reset", "input")
HEALTH_TIMES = ("bridge_age_ms", "frame_ms", "max_gap_ms")
STATES = ("awaiting_operator", "running", "paused", "stopped", "complete", "faulted")


def health_view(raw):
    require(isinstance(raw, dict) and set(raw) == set(HEALTH_FLAGS+HEALTH_TIMES), "health_invalid")
    require(all(type(raw[k]) is bool for k in HEALTH_FLAGS), "health_invalid")
    require(all(type(raw[k]) in (int, float) and math.isfinite(raw[k]) and raw[k] >= 0 for k in HEALTH_TIMES), "health_invalid")
    faults = [k+"_unavailable" for k in HEALTH_FLAGS if not raw[k]]
    if raw["bridge_age_ms"] > 250:
        faults.append("bridge_stale")
    if raw["max_gap_ms"] > 250:
        faults.append("frame_freeze")
    return dict(raw), faults


class Console:
    def __init__(self, catalog, engine, audit, clock=utc_now):
        self.catalog, self.engine, self.audit, self.clock = catalog, engine, audit, clock
        self.visit = None
        self.overrides = {}
        self.comfort = False
        self.phone = False
        self.signoff = ""
        self.deviations = []
        self.started = self.ended = ""
        if hasattr(engine, "receipt_sink"):
            engine.receipt_sink = lambda record: self.audit.append("engine_receipt", "engine", record)

    def load(self, alias, staff):
        if self.visit is not None:
            try:
                require(self.engine.snapshot()["engine_state"] in ("awaiting_operator", "stopped", "complete"), "visit_active")
            except ConsoleFault as fault:
                # Restart revokes prior admission, but permit verified reload.
                if str(fault) != "engine_restarted_reload":
                    raise
        require(alias in self.catalog, "visit_unknown")
        visit = self.catalog[alias]()
        self.audit.append("load_requested", staff, dict(schedule=visit.schedule_hash))
        self.engine.bind(visit)
        self.visit = visit
        self.overrides.clear()
        self.comfort = self.phone = False
        self.signoff = self.started = self.ended = ""
        self.deviations = []
        return self.snapshot()

    def snapshot(self):
        base = dict(loaded=self.visit is not None, choices=sorted(self.catalog), audit_ok=not self.audit.failed)
        if self.visit is None:
            return masked(base)
        v = self.visit
        raw = self.engine.snapshot()
        require(raw["engine_state"] in STATES, "engine_invalid")
        health, faults = health_view(raw["health"])
        counts = raw["completed_counts"]
        require(len(counts) == len(v.rows) and all(type(x) is int and x >= 0 for x in counts), "count_invalid")
        require(all(x <= int(row["expected_count"]) for x, row in zip(counts, v.rows)), "count_invalid")
        admission = raw["admission"]
        require(set(admission) == {"verified", "old_hashes_ok", "locks_ok"} and all(type(x) is bool for x in admission.values()), "engine_invalid")
        matches = (raw["run_sheet_manifest_sha256"], raw["schedule_sha256"], raw["package_sha256"]) == (v.manifest_hash, v.schedule_hash, v.package_hash)
        if not matches or not admission["verified"]:
            faults.append("hash_mismatch")
        if not admission["old_hashes_ok"]:
            faults.append("old_hash_mismatch")
        if not admission["locks_ok"]:
            faults.append("locks_unavailable")
        now = self.clock()
        window = visit_window(v.study, v.visit, v.anchors, now.astimezone().date(), v.role, now)
        for fault in window["faults"]:
            if self.overrides.get(fault) != now.astimezone().date().isoformat():
                faults.append(fault)
        if not self.comfort:
            faults.append("comfort_pending")
        if not self.phone:
            faults.append("phone_pending")
        if self.audit.failed:
            faults.append("audit_failed")
        rows = [dict(block=row["block"], expected=int(row["expected_count"]), actual=n)
                for row, n in zip(v.rows, counts)]
        base.update(study=v.study, participant=v.participant, visit=v.visit, book=v.book, demo=v.demo,
                    state=raw["engine_state"], rows=rows, health=health, faults=faults, window=window,
                    can_start=not faults and raw["engine_state"] in ("awaiting_operator", "paused"),
                    comfort=self.comfort, phone=self.phone, signoff=self.signoff,
                    deviations=list(self.deviations))
        return masked(base)

    def command(self, action, staff, payload):
        code(staff)
        require(self.visit is not None, "visit_not_loaded")
        require(isinstance(payload, dict), "request_invalid")
        if action == "checks":
            require(set(payload) == {"comfort", "phone"} and all(type(x) is bool for x in payload.values()), "request_invalid")
            self.audit.append("checks", staff, payload)
            self.comfort, self.phone = payload["comfort"], payload["phone"]
        elif action == "deviation":
            require(set(payload) == {"reason", "note"}, "request_invalid")
            require(payload["reason"] in ("visit_window", "pair_window", "procedure", "technical"), "reason_invalid")
            note = payload["note"]
            require(isinstance(note, str) and 1 <= len(note.strip()) <= 400 and all(ord(c) >= 32 for c in note), "note_invalid")
            masked(note)
            ident = self.audit.append("deviation", staff, payload)
            self.deviations.append(dict(id=ident, reason=payload["reason"], note=note))
            self.overrides[payload["reason"]] = self.clock().astimezone().date().isoformat()
        elif action == "signoff":
            require(not payload, "request_invalid")
            view = self.snapshot()
            require(view["state"] in ("complete", "stopped"), "visit_active")
            self.audit.append("signoff", staff, dict(counts=[r["actual"] for r in view["rows"]]))
            self.signoff = code(staff)
            self.ended = self.clock().isoformat()
        else:
            require(action in ("start", "pause", "resume", "stop") and not payload, "request_invalid")
            if action in ("start", "resume"):
                view = self.snapshot()
                require(view["can_start"], "start_blocked")
            self.audit.append(action+"_requested", staff, dict(schedule=self.visit.schedule_hash))
            receipt = self.engine.command(action)
            self.audit.append(action+"_acknowledged", staff, receipt)
            if action == "start" and not self.started:
                self.started = self.clock().isoformat()
        return self.snapshot()

    def run_sheet(self):
        view = self.snapshot()
        rows = []
        for row, observed in zip(self.visit.rows, view["rows"]):
            # Feed v1 has visit-level request times, not block boundary receipts.
            # Leave template block times blank until the engine supplies them.
            rows.append(dict(row, actual_count=str(observed["actual"]), start_time="", end_time="",
                             comfort_check="yes" if self.comfort else "", phone_locked="yes" if self.phone else "",
                             deviations=";".join(d["id"] for d in self.deviations), operator_signoff=self.signoff))
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=RUN_SHEET_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        return masked(stream.getvalue()).encode()

    def deviation_csv(self):
        # The external deviation header is unavailable: export explicitly provisional.
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(("deviation_id", "timestamp", "protocol_version", "staff_code", "reason", "note"))
        allowed = {d["id"] for d in self.deviations}
        for row in self.audit.rows:
            if row["event"] == "deviation" and str(row["sequence"]) in allowed:
                text = row["details"]["note"]
                if text.lstrip().startswith(("=", "+", "-", "@")):
                    text = "'"+text
                writer.writerow((row["sequence"], row["utc"], row["protocol"], row["staff"], row["details"]["reason"], text))
        return masked(stream.getvalue()).encode()
