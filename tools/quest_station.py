"""Read-only, explicitly selected Quest inventory and bounded battery observations.

No settings writes, network setup, account operations, installation or launch.
Real per-unit output must use ignored *.local.csv files. Never print serials.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PACKAGE = "org.acousticvocab.experiment"
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
SERIAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
FIELDS = ["record_status", "observed_utc", "station_id", "unit_id", "unit_role", "hardware_serial",
          "model", "os_incremental", "os_fingerprint", "security_patch", "android_release",
          "installed_version_name", "installed_version_code", "expected_build_id", "expected_commit_sha",
          "expected_apk_sha256", "installed_apk_sha256", "installed_artifact_match", "battery_percent",
          "battery_health_raw_code", "battery_status_raw_code", "lab_account_confirmed", "developer_mode_confirmed",
          "os_deferral_method", "notifications_suppressed", "stationary_boundary_confirmed", "audio_route",
          "system_volume_level", "automatic_volume_control", "sleep_setting", "idle_observation_minutes",
          "idle_prompts_seen", "focus_fault_observed", "measured_runtime_minutes", "booking_minutes",
          "buffer_minutes", "agreed_start_min_percent", "operator_signoff"]
BATTERY_FIELDS = ["source_kind", "unit_id", "observed_utc", "elapsed_seconds", "battery_percent",
                  "status_raw_code", "health_raw_code", "ac_powered", "usb_powered", "wireless_powered",
                  "app_process_present"]


class ObservationError(ValueError):
    pass


def identifier(value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise ObservationError("invalid_logical_identifier")
    return value


def private_output(path: Path) -> Path:
    if not path.name.endswith(".local.csv"):
        raise ObservationError("use_ignored_local_csv_output")
    if path.exists() or path.is_symlink():
        raise ObservationError("evidence_output_already_exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def safe_cell(value: object) -> str:
    value = str(value)
    # Prevent spreadsheet formula execution and multiline/invisible metadata.
    if len(value) > 1024 or any(ord(c) < 32 or ord(c) > 126 for c in value) or value.startswith(("=", "+", "-", "@")):
        raise ObservationError("unsafe_metadata_cell")
    return value


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class Adb:
    def __init__(self, executable: Path, serial: str | None = None):
        self.executable = str(executable)
        self.serial = serial
        if serial is not None and not SERIAL.fullmatch(serial):
            raise ObservationError("invalid_device_selector")

    def call(self, *args: str, selected: bool = True, timeout: int = 30) -> str:
        if selected and self.serial is None:
            raise ObservationError("explicit_device_selector_required")
        allowed = args in (("devices",), ("get-state",), ("shell", "dumpsys", "battery"),
                           ("shell", "dumpsys", "package", PACKAGE), ("shell", "pm", "path", PACKAGE),
                           ("shell", "pidof", PACKAGE))
        allowed |= len(args) == 3 and args[:2] == ("shell", "getprop") and args[2] in {
            "ro.serialno", "ro.product.model", "ro.build.version.incremental", "ro.build.fingerprint",
            "ro.build.version.security_patch", "ro.build.version.release"}
        allowed |= len(args) == 3 and args[0] == "pull" and bool(re.fullmatch(r"/data/app/[A-Za-z0-9._/+=~\-]+/base\.apk", args[1]))
        if not allowed or (not selected and args != ("devices",)):
            raise ObservationError("read_only_command_not_allowed")
        command = [self.executable] + (["-s", self.serial] if selected else []) + list(args)
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ObservationError("adb_unavailable_or_timed_out") from exc
        if result.returncode:
            raise ObservationError("adb_command_failed_output_suppressed")
        return result.stdout.strip()

    def require_device(self) -> None:
        if self.call("get-state") != "device":
            raise ObservationError("selected_device_not_authorized_and_connected")


def parse_battery(text: str) -> dict:
    if "UPDATES STOPPED" in text.upper():
        raise ObservationError("battery_service_overridden_measurement_invalid")
    values = {}
    for line in text.splitlines():
        if ":" in line:
            key, value = line.strip().split(":", 1)
            if key in values:
                raise ObservationError("duplicate_battery_field")
            values[key] = value.strip()
    try:
        level, scale = int(values["level"]), int(values["scale"])
        if not 0 <= level <= scale or scale <= 0:
            raise ValueError()
        result = {"battery_percent": level * 100 / scale, "status_raw_code": int(values["status"]),
                  "health_raw_code": int(values["health"])}
        for key in ("AC", "USB", "Wireless"):
            flag = values[key + " powered"].lower()
            if flag not in ("true", "false"):
                raise ValueError()
            result[key.lower() + "_powered"] = flag == "true"
        return result
    except (KeyError, ValueError, ZeroDivisionError) as exc:
        raise ObservationError("battery_fields_unavailable_or_invalid") from exc


def expected_artifact(manifest_path: Path) -> tuple[dict, str]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        identity = manifest["build_identity"]
        files = [f for f in manifest["files"] if f["path"] == "experiment.apk"]
        if (manifest["result"] != "Succeeded" or type(manifest["errors"]) is not int or manifest["errors"] != 0
                or manifest["development_build"] is not False or identity["dirty_source"] is not False
                or identity["target"] != "Android" or len(files) != 1):
            raise ValueError()
        digest = files[0]["sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or not re.fullmatch(r"[0-9a-f]{40}", identity["commit_sha"]):
            raise ValueError()
        identifier(identity["build_id"])
        return identity, digest
    except (KeyError, ValueError, TypeError) as exc:
        raise ObservationError("invalid_verified_android_manifest") from exc


def inventory(adb: Adb, station: str, unit: str, role: str, manifest: Path, output: Path) -> dict:
    identifier(station); identifier(unit)
    if role not in ("station", "spare"):
        raise ObservationError("invalid_unit_role")
    identity, expected_hash = expected_artifact(manifest)
    private_output(output)
    adb.require_device()
    row = dict.fromkeys(FIELDS, "")
    row.update(record_status="observed_pending_operator", observed_utc=utc(), station_id=station, unit_id=unit,
               unit_role=role, expected_build_id=identity["build_id"], expected_commit_sha=identity["commit_sha"],
               expected_apk_sha256=expected_hash)
    for field, prop in {"hardware_serial": "ro.serialno", "model": "ro.product.model", "os_incremental": "ro.build.version.incremental",
                        "os_fingerprint": "ro.build.fingerprint", "security_patch": "ro.build.version.security_patch",
                        "android_release": "ro.build.version.release"}.items():
        row[field] = safe_cell(adb.call("shell", "getprop", prop))
        if not row[field]:
            raise ObservationError("required_device_property_unavailable")
    battery = parse_battery(adb.call("shell", "dumpsys", "battery"))
    row.update(battery_percent=battery["battery_percent"], battery_health_raw_code=battery["health_raw_code"],
               battery_status_raw_code=battery["status_raw_code"])
    package = adb.call("shell", "dumpsys", "package", PACKAGE)
    version = re.search(r"\bversionName=([^\r\n]+)", package)
    code = re.search(r"\bversionCode=(\d+)", package)
    if not version or not code:
        raise ObservationError("app_not_installed_or_version_unavailable")
    row.update(installed_version_name=safe_cell(version[1].strip()), installed_version_code=code[1])
    paths = adb.call("shell", "pm", "path", PACKAGE).splitlines()
    if len(paths) != 1 or not re.fullmatch(r"package:/data/app/[A-Za-z0-9._/+=~\-]+/base\.apk", paths[0]):
        raise ObservationError("unexpected_or_split_installed_apk_path")
    evidence = output.parent / (output.stem + ".local.data")
    evidence.mkdir(exist_ok=False)
    installed = evidence / "installed-base.apk"
    adb.call("pull", paths[0][len("package:"):], str(installed.resolve()), timeout=120)
    digest = hashlib.sha256(installed.read_bytes()).hexdigest()
    row.update(installed_apk_sha256=digest, installed_artifact_match=str(digest == expected_hash).lower())
    with output.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS); writer.writeheader()
        writer.writerow({key: safe_cell(value) for key, value in row.items()})
    return {"station_id": station, "unit_id": unit, "installed_artifact_match": digest == expected_hash,
            "operator_configuration_and_hardware_checks": "pending"}


def read_record(path: Path) -> dict:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != FIELDS:
            raise ObservationError("provisioning_columns_mismatch")
        rows = list(reader)
    if len(rows) != 1 or None in rows[0] or any(value is None for value in rows[0].values()):
        raise ObservationError("expected_one_complete_provisioning_row")
    for value in rows[0].values():
        safe_cell(value)
    return rows[0]


def compare(baseline: Path, current: Path) -> dict:
    expected, actual = read_record(baseline), read_record(current)
    fields = ["station_id", "unit_id", "hardware_serial", "os_incremental", "os_fingerprint", "security_patch",
              "expected_build_id", "expected_commit_sha", "installed_apk_sha256"]
    changed = [key for key in fields if not expected[key] or expected[key] != actual[key]]
    if actual["installed_artifact_match"] != "true":
        changed.append("installed_artifact_match")
    return {"matches_recorded_identity": not changed, "changed_fields": changed, "manual_settings_not_reobserved": True}


def battery_run(adb: Adb, unit: str, output: Path, duration: float, interval: float) -> dict:
    identifier(unit)
    if not math.isfinite(duration) or not 1 <= duration <= 28800 or not math.isfinite(interval) or not 1 <= interval <= 300 or interval > duration:
        raise ObservationError("battery_run_bounds_invalid")
    private_output(output); adb.require_device()
    start = time.monotonic(); count = 0
    with output.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=BATTERY_FIELDS); writer.writeheader(); stream.flush()
        while True:
            battery = parse_battery(adb.call("shell", "dumpsys", "battery"))
            # Process presence is observable; it does not establish foreground view/comfort.
            try:
                present = bool(re.fullmatch(r"\d+(?: \d+)*", adb.call("shell", "pidof", PACKAGE)))
            except ObservationError:
                present = False
            elapsed = time.monotonic() - start
            row = dict(source_kind="adb_read_only", unit_id=unit, observed_utc=utc(), elapsed_seconds=round(elapsed, 6),
                       app_process_present=str(present).lower(), **{k: str(v).lower() if isinstance(v, bool) else v for k, v in battery.items()})
            writer.writerow(row); stream.flush(); count += 1
            if elapsed >= duration:
                break
            time.sleep(min(interval, duration - elapsed))
    return {"unit_id": unit, "samples": count, "qualification": "observations_only"}


def assess(path: Path, booking: float, buffer: float, max_gap: float) -> dict:
    if not all(math.isfinite(x) for x in (booking, buffer, max_gap)) or booking <= 0 or buffer < 0 or max_gap <= 0:
        raise ObservationError("assessment_bounds_invalid")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != BATTERY_FIELDS:
            raise ObservationError("battery_columns_mismatch")
        rows = list(reader)
    if len(rows) < 2:
        raise ObservationError("at_least_two_observations_required")
    try:
        times = [float(r["elapsed_seconds"]) for r in rows]
        levels = [float(r["battery_percent"]) for r in rows]
        if not all(math.isfinite(x) and x >= 0 for x in times) or not all(math.isfinite(x) and 0 <= x <= 100 for x in levels):
            raise ValueError()
        if len({r["unit_id"] for r in rows}) != 1 or any(r["source_kind"] != "adb_read_only" for r in rows):
            raise ValueError()
        for r in rows:
            for field in ("ac_powered", "usb_powered", "wireless_powered", "app_process_present"):
                if r[field] not in ("true", "false"):
                    raise ValueError()
        gaps = [b-a for a, b in zip(times, times[1:])]
        if min(gaps) <= 0:
            raise ValueError()
    except (KeyError, ValueError, TypeError) as exc:
        raise ObservationError("invalid_battery_observations") from exc
    reasons = []
    if max(gaps) > max_gap: reasons.append("observation_gap_exceeded")
    if any(r[k] != "false" for r in rows for k in ("ac_powered", "usb_powered", "wireless_powered")): reasons.append("external_power_present")
    if any(r["status_raw_code"] != "3" for r in rows): reasons.append("not_continuously_reported_discharging")
    if any(r["app_process_present"] != "true" for r in rows): reasons.append("app_process_missing")
    if levels[0] != 100: reasons.append("did_not_start_at_reported_full_charge")
    if any(b > a for a, b in zip(levels, levels[1:])): reasons.append("charge_level_increased")
    observed = times[-1] - times[0]; required = (booking + buffer) * 60
    if observed < required: reasons.append("observed_duration_too_short")
    return {"discharge_observation_covers_requested_duration": not reasons, "reasons": reasons,
            "observed_seconds": observed, "required_seconds": required, "maximum_gap_seconds": max(gaps),
            "percent_threshold": None, "qualification": "operator_review_required_no_extrapolation",
            "limitations": ["Process presence does not prove the intended app view/workload.",
                            "Android health code is not battery capacity or state-of-health measurement.",
                            "No remaining runtime or safe charge threshold is extrapolated."]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest="command", required=True)
    listing=sub.add_parser("list"); listing.add_argument("--adb",type=Path,required=True)
    inv=sub.add_parser("inventory")
    for option in ("adb", "app-manifest", "output"): inv.add_argument("--"+option,type=Path,required=True)
    for option in ("serial", "station-id", "unit-id"): inv.add_argument("--"+option,required=True)
    inv.add_argument("--role",choices=("station","spare"),required=True)
    comp=sub.add_parser("compare"); comp.add_argument("--baseline",type=Path,required=True);comp.add_argument("--current",type=Path,required=True)
    bat=sub.add_parser("battery");bat.add_argument("--adb",type=Path,required=True);bat.add_argument("--serial",required=True);bat.add_argument("--unit-id",required=True);bat.add_argument("--output",type=Path,required=True)
    bat.add_argument("--duration-seconds",type=float,required=True);bat.add_argument("--interval-seconds",type=float,required=True)
    report=sub.add_parser("assess");report.add_argument("--samples",type=Path,required=True)
    for option in ("booking-minutes","buffer-minutes","max-gap-seconds"): report.add_argument("--"+option,type=float,required=True)
    args=parser.parse_args()
    try:
        if args.command=="list":
            lines=Adb(args.adb).call("devices",selected=False).splitlines(); states=[line.split()[1] for line in lines if len(line.split())==2 and not line.startswith("List ")]
            result={"connected_authorized":states.count("device"),"unauthorized":states.count("unauthorized"),"offline":states.count("offline"),"identifiers":"suppressed"}
        elif args.command=="inventory": result=inventory(Adb(args.adb,args.serial),args.station_id,args.unit_id,args.role,args.app_manifest,args.output)
        elif args.command=="compare": result=compare(args.baseline,args.current)
        elif args.command=="battery": result=battery_run(Adb(args.adb,args.serial),args.unit_id,args.output,args.duration_seconds,args.interval_seconds)
        else: result=assess(args.samples,args.booking_minutes,args.buffer_minutes,args.max_gap_seconds)
        print(json.dumps(result,indent=2))
        for key in ("installed_artifact_match", "matches_recorded_identity", "discharge_observation_covers_requested_duration"):
            if result.get(key) is False:
                return 2
        return 0
    except (ObservationError,OSError,KeyboardInterrupt) as exc:
        print(json.dumps({"result":"incomplete","reason":str(exc) if isinstance(exc,ObservationError) else "operation_interrupted_or_local_io_failed"}),file=sys.stderr); return 2


if __name__=="__main__":
    raise SystemExit(main())
