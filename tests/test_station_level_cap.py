"""Station validator: the install-time coupler measurement (decision I7)."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools import validate_station

ROOT = Path(__file__).resolve().parents[1]

EXAMPLE = json.loads((ROOT / "apparatus/examples/station.example.json").read_text())


def provisioned(level=True):
    value = copy.deepcopy(EXAMPLE)
    value["provisioning_status"] = "provisioned"
    value["protocol_version"] = "engineering-test"
    value["audio"]["route"] = "route-01"
    if level:
        value["audio"]["level_calibration"] = {"route": "route-01", "ear_peak_dba_at_0dbfs": 104.5,
                                               "measured_on": "2026-12-15", "coupler_id": "coupler-01"}
    return value


def check(tmp_path, value):
    path = tmp_path / "station.local.json"
    path.write_text(json.dumps(value))
    return validate_station.validate(path, "engineering-test", "standalone_quest")


def test_measured_station_validates(tmp_path):
    """REQ-AUD-09: a provisioned station with its coupler measurement for the configured route validates."""
    assert check(tmp_path, provisioned())["audio"]["level_calibration"]["ear_peak_dba_at_0dbfs"] == 104.5


def test_missing_measurement_is_refused(tmp_path):
    """REQ-AUD-03: a provisioned station without the coupler measurement is refused (no study sound without it)."""
    with pytest.raises(ValueError, match="LEVEL_CAP_OFFSET_MISSING"):
        check(tmp_path, provisioned(level=False))


def test_measurement_on_another_route_is_refused(tmp_path):
    """REQ-AUD-03: a measurement taken on another audio route is refused; a route change needs a new measurement."""
    value = provisioned()
    value["audio"]["route"] = "route-02"
    with pytest.raises(ValueError, match="LEVEL_CAP_ROUTE_MISMATCH"):
        check(tmp_path, value)


@pytest.mark.parametrize("field,bad", [("ear_peak_dba_at_0dbfs", 200), ("ear_peak_dba_at_0dbfs", "104.5"),
                                       ("coupler_id", ""), ("measured_on", "15/12/2026"), ("extra", 1)])
def test_malformed_measurement_is_refused(tmp_path, field, bad):
    """REQ-AUD-09: a malformed coupler measurement fails schema validation."""
    value = provisioned()
    value["audio"]["level_calibration"][field] = bad
    with pytest.raises(ValueError):
        check(tmp_path, value)


def test_impossible_date_is_refused(tmp_path):
    """REQ-AUD-09: the measurement date must be a real calendar date."""
    value = provisioned()
    value["audio"]["level_calibration"]["measured_on"] = "2026-02-30"
    with pytest.raises(ValueError, match="LEVEL_CAP_OFFSET_INVALID"):
        check(tmp_path, value)


def test_command_line_names_the_level_cap_code(tmp_path):
    """REQ-AUD-03: the command line names the refusal code and prints no configuration values."""
    path = tmp_path / "station.local.json"
    path.write_text(json.dumps(provisioned(level=False)))
    result = subprocess.run([sys.executable, str(ROOT / "tools/validate_station.py"), str(path),
                             "--protocol", "engineering-test", "--topology", "standalone_quest"],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "LEVEL_CAP_OFFSET_MISSING" in result.stderr and "route-01" not in result.stderr
