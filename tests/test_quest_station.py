"""Synthetic parser/contract tests. These are not headset measurements."""
import csv
import hashlib
import json
from pathlib import Path

import pytest

from tools import quest_station as q


BATTERY = """Current Battery Service state:
  AC powered: false
  USB powered: false
  Wireless powered: false
  status: 3
  health: 2
  level: 100
  scale: 100
"""


def test_battery_reads_level_and_preserves_raw_health_code():
    result = q.parse_battery(BATTERY.replace("level: 100", "level: 91"))
    assert result["battery_percent"] == 91
    assert result["health_raw_code"] == 2
    assert result["usb_powered"] is False


@pytest.mark.parametrize("text", [BATTERY + "  level: 50\n", BATTERY.replace("scale: 100", "scale: 0"),
    BATTERY.replace("level: 100", "level: 101"), BATTERY.replace("USB powered: false", "USB powered: unavailable"),
    "UPDATES STOPPED -- use reset to restart\n" + BATTERY])
def test_unusable_or_overridden_battery_values_fail_closed(text):
    with pytest.raises(q.ObservationError): q.parse_battery(text)


@pytest.mark.parametrize("cell", ["=HYPERLINK(x)", "+SUM(1)", "@value", "safe\nsecond-row", "\tvalue"])
def test_inventory_cells_cannot_execute_spreadsheet_formulas(cell):
    with pytest.raises(q.ObservationError): q.safe_cell(cell)


@pytest.mark.parametrize("args", [("reboot",), ("shell", "settings", "put", "system", "volume_music", "5"),
    ("shell", "dumpsys", "battery", "set", "level", "100"), ("tcpip", "5555"), ("install", "app.apk"),
    ("shell", "getprop", "unapproved_property"), ("pull", "/sdcard/participant-data", "local")])
def test_read_only_whitelist_blocks_mutating_or_unscoped_adb_commands(args):
    with pytest.raises(q.ObservationError, match="read_only_command_not_allowed"):
        q.Adb(Path("synthetic-unused-adb"), "synthetic-unit").call(*args)


def manifest(tmp_path):
    content = b"synthetic APK fixture, never installed"
    value = {"result": "Succeeded", "errors": 0, "development_build": False,
             "build_identity": {"build_id": "synthetic-build", "commit_sha": "a" * 40, "target": "Android", "dirty_source": False},
             "files": [{"path": "experiment.apk", "sha256": hashlib.sha256(content).hexdigest()}]}
    path = tmp_path / "synthetic-manifest.json"; path.write_text(json.dumps(value))
    return path, content


class FakeAdb:
    def __init__(self, content): self.content = content; self.commands = []
    def require_device(self): pass
    def call(self, *args, **kwargs):
        self.commands.append(args)
        if args[:2] == ("shell", "getprop"): return "synthetic-" + args[2]
        if args == ("shell", "dumpsys", "battery"): return BATTERY
        if args[:3] == ("shell", "dumpsys", "package"): return "versionName=0.1.0\nversionCode=1 minSdk=32"
        if args[:3] == ("shell", "pm", "path"): return "package:/data/app/synthetic/base.apk"
        if args[0] == "pull": Path(args[2]).write_bytes(self.content); return ""
        raise AssertionError(args)


def test_inventory_hashes_installed_apk_and_keeps_manual_claims_empty(tmp_path):
    path, content = manifest(tmp_path); fake = FakeAdb(content); output = tmp_path / "unit.local.csv"
    result = q.inventory(fake, "synthetic-station", "synthetic-unit", "station", path, output)
    assert result["installed_artifact_match"] is True
    row = q.read_record(output)
    assert row["hardware_serial"] == "synthetic-ro.serialno"
    assert row["operator_signoff"] == row["notifications_suppressed"] == row["agreed_start_min_percent"] == ""
    assert row["record_status"] == "observed_pending_operator"
    assert q.compare(output, output)["matches_recorded_identity"] is True
    with pytest.raises(q.ObservationError, match="already_exists"):
        q.inventory(fake, "synthetic-station", "synthetic-unit", "station", path, output)


def test_same_package_version_does_not_mask_wrong_apk(tmp_path):
    path, _ = manifest(tmp_path); output = tmp_path / "mismatch.local.csv"
    result = q.inventory(FakeAdb(b"different synthetic APK"), "synthetic-station", "synthetic-unit", "station", path, output)
    assert result["installed_artifact_match"] is False
    assert q.compare(output, output)["changed_fields"] == ["installed_artifact_match"]


def test_unknown_source_cleanliness_is_not_a_verified_release(tmp_path):
    path, _ = manifest(tmp_path)
    value = json.loads(path.read_text()); value["build_identity"]["dirty_source"] = None
    path.write_text(json.dumps(value))
    with pytest.raises(q.ObservationError, match="invalid_verified_android_manifest"):
        q.expected_artifact(path)


def test_public_or_existing_output_rejected(tmp_path):
    with pytest.raises(q.ObservationError): q.private_output(tmp_path / "public.csv")
    path = tmp_path / "existing.local.csv"; path.write_text("preserve")
    with pytest.raises(q.ObservationError): q.private_output(path)
    assert path.read_text() == "preserve"


def samples(tmp_path, *, power="false", missing="false", end=4800, gap=60, start=100):
    rows=[]
    for t in range(0,end+1,gap):
        rows.append(dict(source_kind="adb_read_only", unit_id="synthetic-unit", observed_utc="synthetic", elapsed_seconds=t,
                         battery_percent=start-t/120, status_raw_code=3, health_raw_code=2, ac_powered="false",
                         usb_powered=power, wireless_powered="false", app_process_present="false" if missing=="true" else "true"))
    path=tmp_path/"synthetic-battery.local.csv"
    with path.open("w",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=q.BATTERY_FIELDS);writer.writeheader();writer.writerows(rows)
    return path


def test_duration_assessment_is_lower_bound_not_runtime_extrapolation(tmp_path):
    result=q.assess(samples(tmp_path),75,5,65)
    assert result["discharge_observation_covers_requested_duration"] is True
    assert result["observed_seconds"]==4800 and result["percent_threshold"] is None
    assert result["qualification"]=="operator_review_required_no_extrapolation"


@pytest.mark.parametrize("change,reason", [({"power":"true"},"external_power_present"),
    ({"missing":"true"},"app_process_missing"),({"end":4200},"observed_duration_too_short"),
    ({"gap":120},"observation_gap_exceeded"),({"start":95},"did_not_start_at_reported_full_charge")])
def test_charging_short_missing_or_gapped_runs_cannot_cover_discharge_requirement(tmp_path,change,reason):
    result=q.assess(samples(tmp_path,**change),75,5,65)
    assert result["discharge_observation_covers_requested_duration"] is False and reason in result["reasons"]


def test_changed_os_build_is_reported_without_exposing_serial(tmp_path):
    path,content=manifest(tmp_path);a=tmp_path/"before.local.csv";b=tmp_path/"after.local.csv"
    q.inventory(FakeAdb(content),"synthetic-station","synthetic-unit","station",path,a)
    row=q.read_record(a);row["os_fingerprint"]="synthetic-new-os"
    with b.open("w",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=q.FIELDS);writer.writeheader();writer.writerow(row)
    report=q.compare(a,b)
    assert report["changed_fields"]==["os_fingerprint"] and "hardware_serial" not in json.dumps(report)
