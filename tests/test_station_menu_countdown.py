"""Station flag for the optional visible menu countdown (public schema and synthetic example only)."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from tools import validate_station

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "apparatus/schemas/station.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
EXAMPLE = json.loads((ROOT / "apparatus/examples/station.example.json").read_text(encoding="utf-8"))
FLAG = "menu_countdown_visible"


def provisioned(tmp_path, **changes):
    value = dict(EXAMPLE, provisioning_status="provisioned", **changes)
    path = tmp_path / "station.local.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_flag_is_an_optional_top_level_boolean_that_defaults_off():
    """REQ-MENU-08: the station flag is boolean, optional and off unless set."""
    spec = SCHEMA["properties"][FLAG]
    assert spec["type"] == "boolean" and spec["default"] is False
    assert FLAG not in SCHEMA["required"]
    assert FLAG not in EXAMPLE
    assert not list(Draft202012Validator(SCHEMA).iter_errors(EXAMPLE))
    assert [name for name in SCHEMA["properties"] if "countdown" in name.lower()] == [FLAG]
    assert FLAG not in SCHEMA["properties"]["audio"]["properties"]


def test_unity_embedded_schema_is_byte_identical():
    """REQ-MENU-08: the Unity loader validates against the same flag definition."""
    embedded = ROOT / "unity/Assets/ExperimentApp/Resources/StationConfigSchema.json"
    assert embedded.read_bytes() == SCHEMA_PATH.read_bytes()


@pytest.mark.parametrize("value", [True, False])
def test_station_validator_accepts_an_explicit_flag(tmp_path, value):
    """REQ-MENU-08: an explicit boolean passes the station validator unchanged."""
    path = provisioned(tmp_path, **{FLAG: value})
    assert validate_station.validate(path, EXAMPLE["protocol_version"], EXAMPLE["topology"])[FLAG] is value


def test_station_validator_does_not_insert_the_default(tmp_path):
    """REQ-MENU-08: an absent flag stays absent; readers treat absence as off."""
    path = provisioned(tmp_path)
    assert FLAG not in validate_station.validate(path, EXAMPLE["protocol_version"], EXAMPLE["topology"])


@pytest.mark.parametrize("value", ["true", "false", 1, 0, None, [], {}])
def test_non_boolean_flag_is_rejected(tmp_path, value):
    """REQ-MENU-08: anything but a JSON boolean fails closed."""
    path = provisioned(tmp_path, **{FLAG: value})
    with pytest.raises(ValueError, match="station schema validation failed"):
        validate_station.validate(path, EXAMPLE["protocol_version"], EXAMPLE["topology"])
